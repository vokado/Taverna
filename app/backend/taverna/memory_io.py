from __future__ import annotations

import copy
from uuid import NAMESPACE_URL, uuid5

from .cards import parse_json
from .core import AppError
from .knowledge import MemoryRecord, RecordInput, player_id, text_hash
from .storage import Mutation, digest, document, encoded, now


class MemoryPackages:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def export(self, story_id, branch_id):
        snap = self.service.snapshot(story_id, branch_id)
        records = []
        legacy = []
        for key, record in snap["records"].items():
            try:
                MemoryRecord.model_validate(record)
            except ValueError:
                legacy.append({"id": key, "raw": record})
                continue
            records.append(record)
        legacy.extend(snap["state"]["payload"].get("memory_import_legacy", []))
        sources = {
            sid: {
                "text_sha256": text_hash(m["text"]),
                "author_id": m.get("author_id"),
                "present_ids": m.get("present_ids", []),
            }
            for sid, m in snap["messages"].items()
            if any(sid in r["source_ids"] for r in records)
        }
        data = {
            "story_id": story_id,
            "branch_id": branch_id,
            "player_id": player_id(story_id),
            "records": records,
            "legacy_records": legacy,
            "actors": {
                cid: str(v["card"]["fields"]["name"])
                for cid, v in snap["story"]["payload"]["character_snapshots"].items()
            },
            "sources": sources,
        }
        return {"format": "taverna-memory", "version": 1, "data": data, "sha256": digest(encoded(data))}

    def preview(self, story_id, branch_id, text, *, actor_map, source_map, allow_external_sources=False):
        package = parse_json(text.encode("utf-8"))
        if (
            package.get("format") != "taverna-memory"
            or package.get("version") != 1
            or not isinstance(package.get("data"), dict)
            or package.get("sha256") != digest(encoded(package["data"]))
        ):
            raise AppError(
                "invalid_memory_package",
                "Формат, версия или контрольная сумма пакета памяти недопустимы.",
                422,
            )
        data = package["data"]
        rows = data.get("records")
        if not isinstance(rows, list) or len(rows) > 10000:
            raise AppError("invalid_memory_package", "Недопустимый список записей памяти.", 422)
        if (
            not isinstance(data.get("sources", {}), dict)
            or any(not isinstance(value, dict) for value in data.get("sources", {}).values())
            or not isinstance(data.get("legacy_records", []), list)
        ):
            raise AppError("invalid_memory_package", "Недопустимые метаданные источников пакета.", 422)
        snap = self.service.snapshot(story_id, branch_id)
        actors = set(snap["story"]["payload"]["character_snapshots"])
        checksum = package["sha256"]
        mapping = {}
        validated = []
        warnings = []
        for raw in rows:
            try:
                record = MemoryRecord.model_validate(raw).model_dump(mode="json")
            except ValueError as exc:
                raise AppError(
                    "invalid_memory_package", "Пакет содержит недопустимую запись памяти.", 422
                ) from exc
            if record["story_id"] != data.get("story_id") or record["branch_id"] != data.get("branch_id"):
                raise AppError("invalid_memory_package", "Запись выходит за область пакета.", 422)
            if record["id"] in mapping:
                raise AppError("invalid_memory_package", "Повторный ID записи.", 422)
            mapping[record["id"]] = str(
                uuid5(NAMESPACE_URL, f"taverna/memory-import/{branch_id}/{checksum}/{record['id']}")
            )
            validated.append(record)

        def actor(value, party=False):
            target = actor_map.get(
                value,
                player_id(story_id)
                if party and value == data.get("player_id")
                else value
                if value in actors
                else None,
            )
            if target not in (actors | {player_id(story_id)} if party else actors):
                raise AppError(
                    "knowledge_scope",
                    "Сопоставьте всех знающих NPC и участников отношений с персонажами целевой истории.",
                    422,
                    actor_id=value,
                )
            return target

        prepared = []
        original_records = {record["id"]: record for record in validated}
        for record in validated:
            body = {key: copy.deepcopy(record[key]) for key in RecordInput.model_fields}
            body["knower_ids"] = list(dict.fromkeys(actor(value) for value in body["knower_ids"]))
            body["subject_ids"] = [
                actor_map.get(value, player_id(story_id) if value == data.get("player_id") else value)
                for value in body["subject_ids"]
            ]
            external = []
            mapped = []
            for sid in body["source_ids"]:
                target = source_map.get(sid, sid if sid in snap["messages"] else None)
                expected = data.get("sources", {}).get(sid, {}).get("text_sha256")
                if target in snap["messages"] and text_hash(snap["messages"][target]["text"]) == expected:
                    mapped.append(target)
                elif allow_external_sources:
                    external.append({"id": sid, "descriptor": data.get("sources", {}).get(sid, {})})
                else:
                    raise AppError(
                        "source_missing",
                        "Сопоставьте исходные сообщения с совпадающим текстом либо явно сохраните их как внешнее происхождение.",
                        422,
                        source_id=sid,
                    )
            body["source_ids"] = list(dict.fromkeys(mapped))
            deps = {}
            for sid in body["source_fact_versions"]:
                if sid not in mapping:
                    raise AppError(
                        "invalid_memory_package", "Отсутствует исходный факт производной записи.", 422
                    )
                deps[mapping[sid]] = 1
                source = original_records[sid]
                if source["version"] != body["source_fact_versions"][sid] or source["status"] != "active":
                    body["status"] = "archived"
                    warnings.append(
                        {
                            "record_id": record["id"],
                            "message_ru": "Устаревшая производная запись сохранена в архиве и требует ручной проверки.",
                        }
                    )
            body["source_fact_versions"] = deps
            body["cause_ids"] = [mapping.get(sid, sid) for sid in body["cause_ids"]]
            if body["relation"]:
                body["relation"]["parties"] = [actor(value, True) for value in body["relation"]["parties"]]
                body["relation"]["episode_ids"] = [
                    mapping.get(sid, sid) for sid in body["relation"]["episode_ids"]
                ]
            body["extras"]["import_origin"] = {
                "package_sha256": checksum,
                "story_id": record["story_id"],
                "branch_id": record["branch_id"],
                "record_id": record["id"],
                "original_version": record["version"],
                "original_history": record["history"],
                "original_provenance": record["provenance"],
                "external_sources": external,
            }
            if external:
                warnings.append(
                    {
                        "record_id": record["id"],
                        "message_ru": "Внешние источники сохранены как происхождение; они не выданы за сообщения целевой истории.",
                    }
                )
            try:
                parsed = RecordInput.model_validate(body).model_dump(mode="json")
            except ValueError as exc:
                raise AppError(
                    "invalid_memory_package", "Сопоставление создаёт недопустимую запись.", 422
                ) from exc
            prepared.append({"id": mapping[record["id"]], "record": parsed})
        return {
            "package_sha256": checksum,
            "records": prepared,
            "legacy_records": data.get("legacy_records", []),
            "warnings": warnings,
            "branch_revision": snap["branch"]["revision"],
        }

    def apply(
        self,
        story_id,
        branch_id,
        expected_revision,
        text,
        *,
        actor_map,
        source_map,
        allow_external_sources,
        confirmed,
        conflict_policy=None,
    ):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите импорт памяти после просмотра.", 422)
        preview = self.preview(
            story_id,
            branch_id,
            text,
            actor_map=actor_map,
            source_map=source_map,
            allow_external_sources=allow_external_sources,
        )
        key = f"memory/imports/{branch_id}/{preview['package_sha256']}.json"
        mapping_fingerprint = digest(
            encoded(
                {
                    "actor_map": actor_map,
                    "source_map": source_map,
                    "allow_external_sources": allow_external_sources,
                }
            )
        )
        existing = self.store.get(key)
        if existing:
            if existing["payload"].get("mapping_fingerprint", mapping_fingerprint) != mapping_fingerprint:
                raise AppError(
                    "memory_import_conflict",
                    "Этот пакет уже импортирован с другим сопоставлением. Проверьте существующие записи и явно измените знания в редакторе либо выберите другую ветвь.",
                    409,
                )
            return {"already_imported": True, **existing["payload"]}
        snap = self.service.snapshot(story_id, branch_id)
        if snap["branch"]["revision"] != expected_revision:
            raise AppError("branch_conflict", "Целевая ветвь изменилась.", 409)
        payload = copy.deepcopy(snap["state"]["payload"])
        pending = list(preview["records"])
        applied = []
        while pending:
            progress = False
            for row in list(pending):
                if not set(row["record"]["source_fact_versions"]) <= set(snap["records"]):
                    continue
                value = RecordInput.model_validate(row["record"])
                record = MemoryRecord(
                    **value.model_dump(mode="json"),
                    id=row["id"],
                    story_id=story_id,
                    branch_id=branch_id,
                    version=1,
                    created_at=now(),
                    updated_at=now(),
                    provenance={
                        "mode": "import",
                        "note": "Подтверждённый импорт памяти",
                        "source_hashes": {
                            sid: text_hash(snap["messages"][sid]["text"])
                            for sid in value.model_dump(mode="json")["source_ids"]
                        },
                    },
                ).model_dump(mode="json")
                if record["id"] in snap["records"]:
                    raise AppError("memory_conflict", "ID импортируемой записи уже существует.", 409)
                conflicts = self.service.conflicts(snap["records"], record)
                if conflicts and conflict_policy != "keep_both":
                    raise AppError(
                        "memory_conflict",
                        "Импорт содержит несовпадающие утверждения. Разрешите конфликт или явно сохраните обе версии.",
                        409,
                        conflicts=conflicts,
                    )
                payload["relationships" if record["level"] == "relationship" else "memory"][record["id"]] = (
                    record
                )
                snap["records"][record["id"]] = record
                applied.append(record["id"])
                pending.remove(row)
                progress = True
            if not progress:
                raise AppError(
                    "invalid_memory_package", "Циклические либо отсутствующие зависимости памяти.", 422
                )
        payload.setdefault("memory_import_legacy", []).extend(copy.deepcopy(preview["legacy_records"]))
        ledger = document(
            "memory_import",
            {
                "story_id": story_id,
                "branch_id": branch_id,
                "package_sha256": preview["package_sha256"],
                "mapping_fingerprint": mapping_fingerprint,
                "record_ids": applied,
            },
        )
        state = self.service.chat.update_state(
            story_id, payload, expected_revision, branch_id, extra_changes=[Mutation(key, ledger, 0)]
        )
        return {
            "record_ids": applied,
            "state_id": state["id"],
            "branch_revision": self.service.chat.branch(story_id, branch_id)["revision"],
            "warnings": preview["warnings"],
        }

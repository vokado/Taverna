from __future__ import annotations

import copy
from uuid import uuid4

from .core import AppError
from .knowledge import KnowledgePolicy, MemoryRecord, RecordInput, player_id, relevance, text_hash
from .storage import now


class MemoryService:
    def __init__(self, chat):
        self.chat, self.store, self.generation = chat, chat.store, chat.generation

    def scheduling_view(self, story_id, branch_id):
        with self.store.view() as view:
            story = view.get(self.chat.story_key(story_id))
            branch = view.get(self.chat.branch_key(story_id, branch_id))
            if not story or not branch:
                raise AppError("branch_missing", "История или ветвь не найдены.", 404)
            state = view.get(self.chat.state_key(story_id, branch["payload"]["state_id"]))
            if not state:
                raise AppError("state_missing", "Состояние памяти отсутствует.", 503)
            head = view.get(self.chat.segments._key(branch_id))
            return {
                "story": story,
                "branch": branch,
                "state": state,
                "message_count": head["payload"]["count"] if head else 0,
            }

    def snapshot(self, story_id, branch_id, *, include_messages=True):
        story = self.chat.story(story_id)
        branch = self.chat.branch(story_id, branch_id)
        state = self.store.get(self.chat.state_key(story_id, branch["payload"]["state_id"]))
        if state is None:
            raise AppError("state_missing", "Состояние памяти отсутствует.", 503)
        messages = {m["id"]: m for m in self.chat._prefix(story_id, branch_id)} if include_messages else {}
        if self.chat.branch(story_id, branch_id)["revision"] != branch["revision"]:
            raise AppError("branch_conflict", "Ветвь изменилась при чтении памяти.", 409)
        records = {**state["payload"]["memory"], **state["payload"]["relationships"]}
        return {"story": story, "branch": branch, "state": state, "messages": messages, "records": records}

    @staticmethod
    def policy(snapshot):
        return KnowledgePolicy(
            snapshot["story"]["id"], snapshot["branch"]["id"], snapshot["records"], snapshot["messages"]
        )

    def list(self, story_id, branch_id, actor=None):
        snap = self.snapshot(story_id, branch_id, include_messages=bool(actor))
        if actor and actor not in snap["story"]["payload"]["character_snapshots"]:
            raise AppError("knowledge_scope", "Персонаж не принадлежит этой истории.", 422)
        policy = self.policy(snap)
        valid = []
        invalid = []
        for key, record in snap["records"].items():
            try:
                MemoryRecord.model_validate(record)
            except ValueError:
                invalid.append({"id": key, "reason": "legacy_or_invalid_record", "raw": record})
                continue
            if not actor or policy.reason(record, actor) is None:
                valid.append(policy.project(record, actor) if actor else record)
        return {
            "records": valid,
            "legacy_records": invalid if not actor else [],
            "branch_revision": snap["branch"]["revision"],
            "state_id": snap["state"]["id"],
            "player_id": player_id(story_id),
        }

    def _prepare(
        self,
        snap,
        value: RecordInput,
        *,
        record_id=None,
        expected_version=0,
        reason,
        mode="manual",
        source_hashes=None,
    ):
        data = value.model_dump(mode="json")
        actors = set(snap["story"]["payload"]["character_snapshots"])
        if not set(data["knower_ids"]) <= actors or (
            data["relation"]
            and not set(data["relation"]["parties"]) <= actors | {player_id(snap["story"]["id"])}
        ):
            raise AppError(
                "knowledge_scope", "Знающие NPC и участники отношений должны принадлежать истории.", 422
            )
        for sid in data["source_ids"]:
            if sid not in snap["messages"]:
                raise AppError("source_missing", "Исходное сообщение отсутствует в ветви.", 409)
            if source_hashes is not None and source_hashes.get(sid) != text_hash(
                snap["messages"][sid]["text"]
            ):
                raise AppError("source_changed", "Исходное сообщение изменилось.", 409)
        policy = self.policy(snap)
        for episode_id in (data.get("relation") or {}).get("episode_ids", []):
            source = snap["records"].get(episode_id)
            if not source or source.get("level") != "episodic":
                raise AppError(
                    "source_missing",
                    "Важный эпизод отношений должен ссылаться на существующую эпизодическую запись.",
                    422,
                )
            data["source_fact_versions"].setdefault(episode_id, source["version"])
        for sid, version in data["source_fact_versions"].items():
            source = snap["records"].get(sid)
            if not source or source.get("version") != version:
                raise AppError("source_changed", "Версия исходного факта изменилась.", 409)
            if any(policy.reason(source, actor) for actor in data["knower_ids"]):
                raise AppError(
                    "knowledge_scope",
                    "Сводка или производная запись раскрывает недоступный исходный факт.",
                    422,
                )
        if record_id:
            pending = list(data["source_fact_versions"])
            seen = set()
            while pending:
                sid = pending.pop()
                if sid == record_id:
                    raise AppError("invalid_memory", "Зависимости памяти образуют цикл.", 422)
                if sid in seen:
                    continue
                seen.add(sid)
                pending.extend(snap["records"].get(sid, {}).get("source_fact_versions", {}))
        old = snap["records"].get(record_id) if record_id else None
        if record_id and old is None:
            raise AppError("memory_missing", "Запись памяти не найдена.", 404)
        if (old["version"] if old else 0) != expected_version:
            raise AppError("memory_version_conflict", "Версия записи изменилась.", 409)
        record = {
            **data,
            "id": record_id or str(uuid4()),
            "story_id": snap["story"]["id"],
            "branch_id": snap["branch"]["id"],
            "version": expected_version + 1,
            "created_at": old["created_at"] if old else now(),
            "updated_at": now(),
            "provenance": {
                "mode": mode,
                "note": reason,
                "source_hashes": {
                    sid: text_hash(snap["messages"][sid]["text"]) for sid in data["source_ids"]
                },
            },
            "history": copy.deepcopy(old.get("history", [])) if old else [],
        }
        if old:
            record["history"].append(
                {
                    "changed_at": now(),
                    "reason": reason,
                    "previous": {k: copy.deepcopy(v) for k, v in old.items() if k != "history"},
                }
            )
        return MemoryRecord.model_validate(record).model_dump(mode="json")

    @staticmethod
    def conflicts(records, proposed):
        if not proposed["claim_key"] or proposed["status"] != "active":
            return []
        return [
            record
            for record in records.values()
            if isinstance(record, dict)
            and record.get("id") != proposed["id"]
            and record.get("status") == "active"
            and record.get("claim_key") == proposed["claim_key"]
            and set(record.get("subject_ids", [])) == set(proposed["subject_ids"])
            and set(record.get("knower_ids", [])) & set(proposed["knower_ids"])
            and record.get("text") != proposed["text"]
        ]

    def save(
        self,
        story_id,
        branch_id,
        expected_revision,
        data: RecordInput,
        *,
        expected_version=0,
        record_id=None,
        reason="Ручная запись владельца",
        conflict_policy=None,
    ):
        snap = self.snapshot(story_id, branch_id)
        if snap["branch"]["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась. Обновите память.", 409)
        record = self._prepare(
            snap, data, record_id=record_id, expected_version=expected_version, reason=reason
        )
        conflicts = self.conflicts(snap["records"], record)
        if conflicts and conflict_policy not in ("keep_both", "supersede"):
            raise AppError(
                "memory_conflict",
                "Обнаружены несовпадающие утверждения. Выберите способ разрешения.",
                409,
                conflicts=conflicts,
            )
        payload = copy.deepcopy(snap["state"]["payload"])
        if conflicts and conflict_policy == "supersede":
            for other in conflicts:
                old = copy.deepcopy(other)
                other = copy.deepcopy(other)
                other["status"] = "superseded"
                other["version"] += 1
                other["updated_at"] = now()
                other["history"].append(
                    {
                        "changed_at": now(),
                        "reason": reason,
                        "previous": {k: v for k, v in old.items() if k != "history"},
                    }
                )
                collection = "relationships" if other["level"] == "relationship" else "memory"
                payload[collection][other["id"]] = other
        for collection in ("memory", "relationships"):
            payload[collection].pop(record["id"], None)
        payload["relationships" if record["level"] == "relationship" else "memory"][record["id"]] = record
        if conflicts:
            payload.setdefault("memory_conflict_decisions", []).append(
                {
                    "versions": {
                        record["id"]: record["version"],
                        **{row["id"]: row["version"] for row in conflicts},
                    },
                    "decision": conflict_policy,
                    "reason": reason,
                    "at": now(),
                }
            )
        state = self.chat.update_state(story_id, payload, expected_revision, branch_id)
        return {
            "record": record,
            "state_id": state["id"],
            "branch_revision": self.chat.branch(story_id, branch_id)["revision"],
            "conflicts_kept": [row["id"] for row in conflicts] if conflict_policy == "keep_both" else [],
        }

    def archive(self, story_id, branch_id, expected_revision, *, min_salience=0.2, min_age_messages=50):
        snap = self.snapshot(story_id, branch_id)
        messages = list(snap["messages"])
        recent = set(messages[max(0, len(messages) - min_age_messages) :])
        protected = set()
        pending = [
            record["id"]
            for record in snap["records"].values()
            if isinstance(record, dict)
            and record.get("pinned")
            and record.get("status") == "active"
            and record.get("id")
        ]
        while pending:
            rid = pending.pop()
            if rid in protected:
                continue
            protected.add(rid)
            pending.extend(snap["records"].get(rid, {}).get("source_fact_versions", {}))
        payload = copy.deepcopy(snap["state"]["payload"])
        archived = []
        for collection in ("memory", "relationships"):
            for rid, record in payload[collection].items():
                try:
                    MemoryRecord.model_validate(record)
                except ValueError:
                    continue
                if (
                    record["status"] != "active"
                    or rid in protected
                    or record["pinned"]
                    or record["salience"] >= min_salience
                    or not record["source_ids"]
                    or set(record["source_ids"]) & recent
                ):
                    continue
                old = {k: copy.deepcopy(v) for k, v in record.items() if k != "history"}
                record.update(status="archived", version=record["version"] + 1, updated_at=now())
                record["history"].append(
                    {"changed_at": now(), "reason": "Архивирование по политике забывания", "previous": old}
                )
                archived.append(rid)
        if archived:
            self.chat.update_state(story_id, payload, expected_revision, branch_id)
        elif snap["branch"]["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась.", 409)
        return {
            "archived_ids": archived,
            "branch_revision": self.chat.branch(story_id, branch_id)["revision"],
        }

    def timeline(self, story_id, branch_id, actor=None):
        snap = self.snapshot(story_id, branch_id)
        policy = self.policy(snap)
        records = [policy.project(record, actor) for record in policy.visible(actor)] if actor else []
        if not actor:
            for record in snap["records"].values():
                try:
                    MemoryRecord.model_validate(record)
                except ValueError:
                    continue
                records.append(record)
        return {
            "entries": sorted(records, key=lambda r: (r.get("story_time", ""), r["created_at"], r["id"])),
            "anchors": {}
            if actor
            else {name: snap["branch"]["payload"][name] for name in ("chapters", "bookmarks", "checkpoints")},
            "branch_revision": snap["branch"]["revision"],
        }

    def search_records(
        self,
        snapshot,
        query,
        actor=None,
        *,
        level=None,
        nature=None,
        subject_id=None,
        source_id=None,
        location_id=None,
        time_from=None,
        time_to=None,
    ):
        policy = self.policy(snapshot)
        candidates = policy.visible(actor) if actor else snapshot["records"].values()
        result = []
        for record in candidates:
            try:
                MemoryRecord.model_validate(record)
            except ValueError:
                continue
            if (
                record["status"] != "active"
                or record["story_id"] != snapshot["story"]["id"]
                or record["branch_id"] != snapshot["branch"]["id"]
            ):
                continue
            if level and record["level"] != level or nature and record["nature"] != nature:
                continue
            if (
                subject_id
                and subject_id not in record["subject_ids"]
                or source_id
                and source_id not in record["source_ids"]
            ):
                continue
            if location_id and location_id != record["location_id"]:
                continue
            time = record["story_time"] or record["created_at"]
            if time_from and time < time_from or time_to and time > time_to:
                continue
            score = relevance(record["text"], query)
            if query and score == 0:
                continue
            result.append(
                {
                    "record": policy.project(record, actor) if actor else record,
                    "text_score": score,
                    "score": score + record["salience"] * 0.1 + int(record["pinned"]),
                }
            )
        return sorted(result, key=lambda row: (-row["score"], row["record"]["id"]))

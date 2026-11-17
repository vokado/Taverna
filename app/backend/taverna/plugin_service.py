"""Shared-queue computation and guarded, atomic internal plugin effects."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from .core import AppError
from .plugin_actions import PluginPlan, PluginRun
from .plugin_command_worker import BRIDGE, pinned_executable
from .plugin_network import execute as execute_network
from .plugin_compute import compute
from .plugin_files import PluginFiles
from .plugin_registry import CATALOG
from .storage import Mutation, document, encoded, safe_path
from .tool_models import NameRun, TemplatePreview


class PluginService:
    def __init__(self, registry, tools, queue):
        self.registry, self.tools, self.queue = registry, tools, queue
        self.store, self.books = registry.store, tools.manuscripts
        self.files = PluginFiles(registry)
        self.store.register("plugin_plan", 1, PluginPlan.model_json_schema())
        queue.handlers["plugin_compute"] = self.run
        queue.handlers["plugin_effect"] = self.execute_external
        queue.preemptible_kinds.add("plugin_compute")

    @staticmethod
    def proposal_key(pid):
        return "automations/plugin-previews/" + str(UUID(str(pid))) + ".json"

    @staticmethod
    def own_key(pid, target):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target):
            raise AppError("plugin_target_invalid", "Некорректный ключ собственной области.", 422)
        return f"automations/plugin-data/{UUID(str(pid))}/{target}.json"

    def target_key(self, kind, pid, target):
        if kind == "own":
            return self.own_key(pid, target)
        return self.tools.key(target) if kind == "note" else self.books.key(target)

    def current(self, payload, view=None):
        return self.registry.require_current(
            payload["plugin_id"],
            payload["plugin_revision"],
            payload["permission_epoch"],
            payload["package_sha256"],
            view,
            catalog_revision=payload["catalog_revision"],
        )

    def permit(self, row, capability, target):
        if not self.registry.allows(row, capability, target):
            raise AppError(
                "plugin_scope_denied", "Нет отдельного разрешения на выбранную область действия.", 403
            )

    @staticmethod
    def parameters(manifest, values):
        fields = {f["key"]: f for f in manifest["fields"]}
        if set(values) - set(fields):
            raise AppError("plugin_parameters", "Переданы необъявленные поля расширения.", 422)
        for key, field in fields.items():
            value = values.get(key)
            if key not in values:
                if field["required"]:
                    raise AppError("plugin_parameters", "Заполните обязательные поля расширения.", 422)
                continue
            valid = {
                "text": isinstance(value, str) and len(value) <= 16000,
                "number": type(value) in (int, float) and abs(value) <= 1e100 and math.isfinite(value),
                "checkbox": type(value) is bool,
                "select": isinstance(value, str) and value in field["options"],
            }[field["type"]]
            if not valid or (field["required"] and isinstance(value, str) and not value.strip()):
                raise AppError("plugin_parameters", "Тип или значение поля не соответствует манифесту.", 422)
        if len(encoded(values)) > 128 * 1024:
            raise AppError("plugin_input_limit", "Поля расширения больше128КиБ.", 413)

    async def enqueue(self, pid, body: PluginRun, idempotency_key):
        payload = {**body.model_dump(mode="json"), "plugin_id": str(UUID(str(pid)))}
        row = await asyncio.to_thread(self.current, payload)
        if any(not f["confirmed"] for f in payload["files"]):
            raise AppError(
                "confirmation_required", "Подтвердите передачу каждого выбранного внешнего файла.", 403
            )
        self.parameters(row["payload"]["manifest"], payload["parameters"])
        if row["payload"]["manifest"]["runtime"] == "trusted_python" and not body.confirmed_full_access_run:
            raise AppError(
                "plugin_trust_required",
                "Каждый ручной запуск Python требует подтверждения полного доступа.",
                403,
            )
        return await self.queue.enqueue(
            "plugin_compute",
            {**payload, "proposal_id": str(uuid4())},
            priority=20,
            idempotency_key=idempotency_key,
            identity=payload,
            scope={"plugin_id": payload["plugin_id"]},
        )

    def inputs(self, payload):
        data, guards = [], {}
        with self.store.view() as view:
            row = self.current(payload, view)
            guards[self.registry.key(row["id"])] = row["revision"]
            guards[CATALOG] = payload["catalog_revision"]
            for read in payload["reads"]:
                kind, target = read["kind"], read["target"]
                self.permit(
                    row, {"own": "own.read", "note": "notes.read", "book": "books.read"}[kind], target
                )
                key = self.target_key(kind, row["id"], target)
                value = view.get(key)
                revision = value["revision"] if value else 0
                if revision != read["expected_revision"]:
                    raise AppError("plugin_input_conflict", "Выбранный источник изменился до запуска.", 409)
                if value and (
                    value["kind"] != {"own": "plugin_data", "note": "tool_entry", "book": "manuscript"}[kind]
                    or kind == "note"
                    and value["payload"]["kind"] not in ("note", "bookmark", "dictionary")
                ):
                    raise AppError(
                        "plugin_data_forbidden", "Источник не относится к разрешённому типу данных.", 403
                    )
                guards[key] = revision
                data.append(
                    {
                        "kind": kind,
                        "target": target,
                        "revision": revision,
                        "value": copy.deepcopy(value["payload"] if value else None),
                    }
                )
                if len(encoded({"parameters": payload["parameters"], "data": data})) > 256 * 1024:
                    raise AppError(
                        "plugin_input_limit", "Выбранные данные больше256КиБ; сократите выборку.", 413
                    )
        request = {"format": 1, "parameters": payload["parameters"], "data": data}
        for selected in payload.get("files", []):
            if not selected["confirmed"]:
                raise AppError("confirmation_required", "Не подтверждено чтение внешнего файла.", 403)
            item = self.files.read(payload, selected["scope"], selected["name"])
            if item["sha256"] != selected["sha256"]:
                raise AppError(
                    "plugin_file_conflict",
                    "Внешний файл изменился после предпросмотра. Выберите его заново.",
                    409,
                )
            request.setdefault("files", []).append({**item, "scope": selected["scope"]})
            if len(encoded(request)) > 256 * 1024:
                raise AppError("plugin_input_limit", "Выбранные данные и файлы больше256КиБ.", 413)
        if len(encoded(request)) > 256 * 1024:
            raise AppError("plugin_input_limit", "Выбранные данные больше256КиБ; сократите выборку.", 413)
        return row, request, guards

    @staticmethod
    def substitute(value, parameters, budget=None, depth=0):
        # Charge before constructing expanded strings; a tiny repeated template
        # must not allocate an unbounded result in the server process.
        budget = budget if budget is not None else {"bytes": 0, "nodes": 0}
        budget["nodes"] += 1
        if depth > 32 or budget["nodes"] > 10000:
            raise AppError(
                "plugin_output_limit", "План слишком глубокий или содержит слишком много элементов.", 413
            )

        def charge(part):
            budget["bytes"] += len(part.encode("utf-8"))
            if budget["bytes"] > 256 * 1024:
                raise AppError("plugin_output_limit", "Развёрнутый план больше256КиБ.", 413)
            return part

        if isinstance(value, str):
            chunks, start = [], 0
            for match in re.finditer(r"\{\{\s*([\w-]+)\s*\}\}", value):
                key = match[1]
                if key not in parameters:
                    raise AppError("plugin_parameters", "В плане использована незаполненная переменная.", 422)
                item = parameters[key]
                chunks.append(charge(value[start : match.start()]))
                chunks.append(charge(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
                start = match.end()
            chunks.append(charge(value[start:]))
            return "".join(chunks)
        if isinstance(value, list):
            return [PluginService.substitute(v, parameters, budget, depth + 1) for v in value]
        if isinstance(value, dict):
            result = {}
            for key, v in value.items():
                charge(key)
                result[key] = PluginService.substitute(v, parameters, budget, depth + 1)
            return result
        charge(json.dumps(value))
        return value

    async def run(self, payload, cancel, emit):
        row, request, guards = await asyncio.to_thread(self.inputs, payload)
        files = await asyncio.to_thread(self.registry.verify, row)
        manifest = row["payload"]["manifest"]
        code = files[manifest["entry"]]
        if manifest["runtime"] == "declarative":
            plan = self.substitute(json.loads(code), payload["parameters"])
            metadata = {"runtime": "declarative"}
        else:
            # Use a sealed snapshot for helper imports: registry delete/upgrade cannot
            # substitute helpers after verification. Trusted code remains full access.
            with TemporaryDirectory(prefix="taverna-plugin-", ignore_cleanup_errors=True) as temporary:
                entry_path = None
                if manifest["runtime"] == "trusted_python":
                    for name, raw in files.items():
                        path = safe_path(Path(temporary), name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(raw)
                    entry_path = safe_path(Path(temporary), manifest["entry"])
                result = await compute(
                    code,
                    request,
                    runtime=manifest["runtime"],
                    cancel=cancel,
                    trusted_full_access=row["payload"]["trusted_full_access_ack"],
                    entry_path=entry_path,
                )
            plan = result["result"]
            metadata = {
                "runtime": manifest["runtime"],
                "isolation": result["isolation"],
                "host_limits": result.get("host_limits"),
                "fuel_used": result.get("fuel_used"),
            }
        if cancel.is_set():
            raise AppError("plugin_cancelled", "Запуск отменён; план не опубликован.", 409)
        try:
            plan = PluginPlan.model_validate(plan).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "plugin_plan_invalid", "Расширение вернуло недопустимый план действий.", 422
            ) from exc
        if len(encoded(plan)) > 256 * 1024:
            raise AppError("plugin_output_limit", "План расширения больше256КиБ.", 413)
        await asyncio.to_thread(self.current, payload)
        rendered_tools = []
        external_actions = [
            action for action in plan["actions"]
            if action["type"] in ("command.exec", "network.request")
        ]
        if external_actions:
            # External effects are not atomic with JSON: exactly one explicit,
            # owner-confirmed effect per independent durable preview.
            if len(external_actions) != 1 or len(plan["actions"]) != 1:
                raise AppError(
                    "plugin_external_mixed",
                    "Внешнее действие требуется подтвердить отдельным планом.", 422
                )
            effect = external_actions[0]
            await asyncio.to_thread(self.validate_external, row, effect)
            field = "external_command" if effect["type"] == "command.exec" else "external_network"
            metadata[field] = {
                **effect,
                "fingerprint": hashlib.sha256(encoded(effect)).hexdigest(),
            }
            changes, write_guards = [], {}
        else:
            changes, write_guards = await asyncio.to_thread(self.plan_effects, row, plan, rendered_tools)
        metadata["tool_previews"] = rendered_tools
        self.merge_guards(guards, write_guards)
        metadata["external_sources"] = [
            {k: f[k] for k in ("scope", "path", "bytes", "sha256")} for f in request.get("files", [])
        ]
        preview = document(
            "plugin_preview",
            {
                "status": "pending",
                "identity": {
                    k: payload[k]
                    for k in (
                        "plugin_id",
                        "plugin_revision",
                        "permission_epoch",
                        "package_sha256",
                        "catalog_revision",
                    )
                },
                "plan": plan,
                "guards": guards,
                "metadata": metadata,
                "effects": [
                    {"key": m.key, "expected_revision": m.expected_revision, "value": m.value}
                    for m in changes
                ],
            },
            entity_id=payload["proposal_id"],
        )
        await asyncio.to_thread(
            self.store.transaction, [Mutation(self.proposal_key(preview["id"]), preview, 0)], guards=guards
        )
        return {"proposal_id": preview["id"], "status": "pending", "message": plan["message"]}

    def validate_external(self, row, effect):
        if effect["type"] == "network.request":
            from .plugin_network import encode_request, validate_url

            validate_url(effect["url"])
            encode_request(effect["method"], effect.get("payload"))
            if not any(
                grant["capability"] == "network.request"
                and effect["url"] in grant["scopes"]
                for grant in row["payload"]["grants"]
            ):
                raise AppError(
                    "plugin_scope_denied", "Нет точного разрешения на этот сетевой адрес.", 403
                )
            return
        if effect["type"] != "command.exec":
            raise AppError("plugin_external_invalid", "Неизвестное внешнее действие.", 422)
        self.permit(row, "commands.exec", effect["executable"])
        if not any(
            grant["capability"] == "commands.exec"
            and grant["full_access_warning_ack"]
            and effect["executable"] in grant["scopes"]
            for grant in row["payload"]["grants"]
        ):
            raise AppError(
                "plugin_trust_required", "Подтвердите полный доступ конкретной команды.", 403
            )
        with pinned_executable(effect["executable"], effect["sha256"]):
            pass

    @staticmethod
    def external_payload(preview):
        metadata = preview["payload"].get("metadata", {})
        effect = metadata.get("external_command") or metadata.get("external_network")
        if not effect:
            raise AppError("plugin_external_missing", "У плана отсутствует внешнее действие.", 409)
        return effect

    def external_job_key(self, preview_id):
        return "plugin-effect/" + str(UUID(str(preview_id)))

    async def approve_external(self, preview_id, body, action_type="command.exec"):
        if not body.confirmed or not body.full_access_warning_ack:
            raise AppError(
                "confirmation_required", "Подтвердите внешнее действие и риск необратимых последствий.", 422
            )
        key = self.proposal_key(preview_id)
        preview = await asyncio.to_thread(self.store.get, key)
        if not preview or preview["kind"] != "plugin_preview":
            raise AppError("plugin_preview_missing", "План расширения не найден.", 404)
        command = self.external_payload(preview)
        if command["type"] != action_type:
            raise AppError("plugin_external_type", "Для этого действия выбрана неправильная операция подтверждения.", 409)
        if command["fingerprint"] != body.fingerprint:
            raise AppError("plugin_command_changed", "Внешнее действие отличается от подтверждённого.", 409)
        if preview["payload"]["status"] == "rejected":
            raise AppError("plugin_preview_used", "Отклонённый план нельзя запустить.", 409)
        if preview["payload"]["status"] == "pending":
            if preview["revision"] != body.expected_revision:
                raise AppError("plugin_preview_conflict", "План изменился. Обновите просмотр.", 409)
            row = await asyncio.to_thread(self.current, preview["payload"]["identity"])
            await asyncio.to_thread(self.validate_external, row, command)
        # Server-owned key: different client keys cannot replay the same preview.
        return await self.queue.enqueue(
            "plugin_effect",
            {"preview_id": str(preview_id), "fingerprint": body.fingerprint},
            priority=10,
            idempotency_key=self.external_job_key(preview_id),
            identity={"preview_id": str(preview_id), "fingerprint": body.fingerprint},
            scope={"plugin_id": preview["payload"]["identity"]["plugin_id"]},
        )

    async def execute_external(self, payload, cancel, emit):
        key = self.proposal_key(payload["preview_id"])
        preview = await asyncio.to_thread(self.store.get, key)
        if not preview or preview["kind"] != "plugin_preview":
            raise AppError("plugin_preview_missing", "План расширения не найден.", 404)
        body = preview["payload"]
        command = self.external_payload(preview)
        if body["status"] != "pending" or command["fingerprint"] != payload["fingerprint"]:
            raise AppError("plugin_preview_used", "Внешнее действие уже обработано или изменено.", 409)
        row = await asyncio.to_thread(self.current, body["identity"])
        await asyncio.to_thread(self.validate_external, row, command)
        if cancel.is_set():
            raise AppError("plugin_cancelled", "Команда отменена до начала запуска.", 409)
        # Resolve the durable queue job ID, so status and command identity can
        # be reconciled after a crash without resubmitting the OS command.
        idempotency = hashlib.sha256(self.external_job_key(payload["preview_id"]).encode()).hexdigest()
        job_key = await asyncio.to_thread(self.store.get, "jobs/keys/" + idempotency + ".json")
        if job_key is None:
            raise AppError("plugin_job_missing", "Запись задачи отсутствует.", 503)
        job_id = job_key["payload"]["job_id"]
        body["status"] = "started"
        body["external_job_id"] = job_id
        await asyncio.to_thread(
            self.store.transaction,
            [Mutation(key, preview, preview["revision"])],
            guards=body["guards"],
            before_commit=lambda: self._before_command(cancel),
        )
        await emit("external_started", {"preview_id": preview["id"], "irreversible": True})
        outcome = None
        try:
            if command["type"] == "network.request":
                outcome = await execute_network(command, cancel)
                status = "observed" if outcome.get("state") == "observed" else "unknown"
            else:
                request = {k: command[k] for k in ("executable", "sha256", "argv", "timeout_seconds")}
                response = await compute(
                    BRIDGE,
                    request,
                    runtime="trusted_python",
                    trusted_full_access=True,
                    timeout=min(30, command["timeout_seconds"] + 6),
                    cancel=cancel,
                )
                outcome = response["result"]
                status = "observed" if outcome.get("state") == "exited" else "unknown"
            return_value = {"status": status, "preview_id": preview["id"], "outcome": outcome}
        except BaseException:
            # Failed/aborted/unknown host may already have performed the effect.
            status = "unknown"
            return_value = None
            raise
        finally:
            # Store the observation regardless of changed grants. It describes
            # an already-started external effect, not a fresh authorization.
            def save():
                current = self.store.get(key)
                if current and current["payload"]["status"] == "started":
                    current["payload"]["status"] = status
                    current["payload"]["observed"] = outcome
                    self.store.transaction([Mutation(key, current, current["revision"])])
            await asyncio.shield(asyncio.to_thread(save))
        return return_value

    def recover_external(self):
        """Turn interrupted started receipts into unknown; never re-run commands."""
        rows, _ = self.store.scan("automations/plugin-previews/", tolerate_corrupt=True)
        recovered = 0
        for item in rows:
            preview = item["value"]
            if preview["payload"].get("status") != "started":
                continue
            job_id = preview["payload"].get("external_job_id")
            job = self.store.get(self.queue.key(job_id)) if job_id else None
            # Active jobs are never considered interrupted during an ordinary
            # browser refresh; recovery runs only once at application startup.
            if job and job["payload"]["state"] in ("queued", "running"):
                continue
            preview["payload"]["status"] = "unknown"
            preview["payload"]["recovery_reason"] = "application_restarted"
            self.store.transaction(
                [Mutation(item["key"], preview, preview["revision"])]
            )
            recovered += 1
        return recovered

    def reconcile_external(self, preview_id, decision):
        if not decision.confirmed:
            raise AppError("confirmation_required", "Подтвердите ручную сверку результата.", 422)
        key = self.proposal_key(preview_id)
        preview = self.store.get(key)
        if not preview or preview["kind"] != "plugin_preview":
            raise AppError("plugin_preview_missing", "План расширения не найден.", 404)
        self.external_payload(preview)
        if preview["revision"] != decision.expected_revision:
            raise AppError("plugin_preview_conflict", "Результат изменился. Обновите сведения.", 409)
        if preview["payload"]["status"] not in ("unknown", "started"):
            raise AppError("plugin_command_not_unknown", "Сверка разрешена только для неясного исхода.", 409)
        job_id = preview["payload"].get("external_job_id")
        job = self.store.get(self.queue.key(job_id)) if job_id else None
        if job and job["payload"]["state"] in ("queued", "running"):
            raise AppError(
                "plugin_command_running",
                "Нельзя сверять результат, пока исходная команда ещё выполняется.", 409
            )
        preview["payload"]["status"] = "reconciled"
        preview["payload"]["reconciliation"] = {
            "outcome": decision.outcome,
            "reason": decision.reason,
            "at": datetime.now(timezone.utc).isoformat(),
            "by": "local_owner",
            "manually_reported": True,
        }
        self.store.transaction([Mutation(key, preview, decision.expected_revision)])
        return self.store.get(key)

    @staticmethod
    def _before_command(cancel):
        if cancel.is_set():
            raise AppError("plugin_cancelled", "Команда отменена до фиксации запуска.", 409)

    @staticmethod
    def merge_guards(target, more):
        for key, revision in more.items():
            if key in target and target[key] != revision:
                raise AppError("plugin_input_conflict", "Источник изменился во время вычисления плана.", 409)
            target[key] = revision

    def plan_effects(self, row, plan, rendered_tools=None):
        changes, guards, targets = [], {}, set()
        for action in plan["actions"]:
            if action["type"].startswith("tool."):
                if not any(
                    g["capability"] == "tools.run" and action["target"] in g["scopes"]
                    for g in row["payload"]["grants"]
                ):
                    raise AppError(
                        "plugin_scope_denied", "Выберите отдельное разрешение на точный инструмент.", 403
                    )
                if action["type"] == "tool.names":
                    body = NameRun.model_validate({k: action[k] for k in NameRun.model_fields})
                    result, extra = self.tools.names_plan(action["target"], body)
                    result["id"] = action["result_id"]
                    key = "manuscripts/tool-runs/" + result["id"] + ".json"
                    if key in targets:
                        raise AppError("plugin_duplicate_effect", "План повторяет результат генератора.", 422)
                    targets.add(key)
                    changes.append(Mutation(key, result, 0))
                    self.merge_guards(guards, {**extra, key: 0})
                    if rendered_tools is not None:
                        rendered_tools.append(
                            {
                                "tool_id": action["target"],
                                "tool_revision": body.expected_revision,
                                "action": "names",
                                "title": "Результат генератора имён",
                                "text": "\n".join(result["payload"]["names"]),
                                "manuscript_id": None,
                                "manuscript_revision": None,
                                "result_id": result["id"],
                            }
                        )
                    continue
                body = TemplatePreview.model_validate({k: action[k] for k in TemplatePreview.model_fields})
                preview, extra = self.tools.preview_plan(action["target"], body)
                data = preview["payload"]
                data["result_id"] = action["result_id"]
                self.permit(
                    row,
                    "notes.write" if data["action"] == "note" else "books.write",
                    "new" if data["action"] == "note" else data["manuscript_id"],
                )
                more, current_guards, _ = self.tools.plan_preview_effects(data)
                self.merge_guards(extra, current_guards)
                for change in more:
                    if change.key in targets:
                        raise AppError(
                            "plugin_duplicate_effect", "Один план не может дважды менять один документ.", 422
                        )
                    targets.add(change.key)
                changes.extend(more)
                self.merge_guards(guards, extra)
                if rendered_tools is not None:
                    rendered_tools.append(
                        {
                            k: data[k]
                            for k in (
                                "tool_id",
                                "tool_revision",
                                "action",
                                "title",
                                "text",
                                "manuscript_id",
                                "manuscript_revision",
                                "result_id",
                            )
                        }
                    )
                continue
            target, expected = action["target"], action["expected_revision"]
            kind = action["type"].split(".")[0]
            self.permit(
                row,
                {"own": "own.write", "note": "notes.write", "book": "books.write"}[kind],
                "new" if expected == 0 and kind != "own" else target,
            )
            key = self.target_key(kind, row["id"], target)
            if key in targets:
                raise AppError(
                    "plugin_duplicate_effect", "Один план не может дважды менять один документ.", 422
                )
            targets.add(key)
            current = self.store.get(key)
            if (current["revision"] if current else 0) != expected:
                raise AppError(
                    "plugin_effect_conflict", "Документ для изменения уже имеет другую редакцию.", 409
                )
            if kind == "own":
                if current and current["kind"] != "plugin_data":
                    raise AppError("plugin_data_forbidden", "Некорректный тип собственной записи.", 403)
                if action["type"] == "own.delete":
                    value = current
                    value["payload"] = {"deleted": True, "value": None}
                else:
                    if len(encoded(action["value"])) > 64 * 1024:
                        raise AppError("plugin_data_limit", "Собственная запись больше64КиБ.", 413)
                    value = current or document("plugin_data", {}, entity_id=str(uuid4()))
                    value["payload"] = {"deleted": False, "value": action["value"]}
                changes.append(Mutation(key, value, expected))
                guards[key] = expected
            elif kind == "note":
                if current and (
                    current["kind"] != "tool_entry"
                    or current["payload"]["kind"] not in ("note", "bookmark", "dictionary")
                ):
                    raise AppError(
                        "plugin_data_forbidden",
                        "Нельзя перезаписать генератор/шаблон через notes.write.",
                        403,
                    )
                more, extra = self.tools.plan(target, expected, action["payload"])
                changes.extend(more)
                self.merge_guards(guards, extra)
                guards[key] = expected
            else:
                more, extra = self.books.plan(
                    target, expected, action["payload"], "Подтверждённый план расширения " + row["id"]
                )
                changes.extend(more)
                self.merge_guards(guards, extra)
                guards[key] = expected
        return changes, guards

    def file_preview(self, pid, body):
        identity = {**body.model_dump(mode="json"), "plugin_id": pid}
        item = self.files.read(identity, body.scope, body.name)
        raw = base64.b64decode(item["content_base64"])
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            text = None
        return {
            "scope": body.scope,
            "name": body.name,
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "text_preview": text[:8000] if text is not None else None,
            "truncated": bool(text is not None and len(text) > 8000),
        }

    def own_data(self, pid, target):
        with self.store.view() as view:
            row = self.registry.get(pid, view)
            self.permit(row, "own.read", target)
            value = view.get(self.own_key(pid, target))
            if value and value["kind"] != "plugin_data":
                raise AppError("plugin_data_forbidden", "Некорректный тип собственной записи.", 403)
            return {
                "target": target,
                "revision": value["revision"] if value else 0,
                "value": value["payload"].get("value") if value else None,
                "deleted": value["payload"].get("deleted", False) if value else False,
            }

    def previews(self, pid):
        self.registry.get(pid)
        rows, errors = self.store.scan("automations/plugin-previews/", tolerate_corrupt=True)
        return {
            "items": [r["value"] for r in rows if r["value"]["payload"]["identity"]["plugin_id"] == pid],
            "errors": errors,
        }

    def decide(self, preview_id, expected, confirmed, accept=True):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите действие из просмотренного плана.", 422)
        key = self.proposal_key(preview_id)
        preview = self.store.get(key)
        if not preview or preview["kind"] != "plugin_preview":
            raise AppError("plugin_preview_missing", "План расширения не найден.", 404)
        p = preview["payload"]
        if p["status"] == ("applied" if accept else "rejected") and expected in (
            preview["revision"],
            preview["revision"] - 1,
        ):
            return preview
        if preview["revision"] != expected:
            raise AppError("plugin_preview_conflict", "План уже изменён. Обновите список.", 409)
        if p["status"] != "pending":
            raise AppError("plugin_preview_used", "План уже обработан.", 409)
        changes, guards = [], {}
        if accept and (
            p.get("metadata", {}).get("external_command")
            or p.get("metadata", {}).get("external_network")
        ):
            raise AppError(
                "plugin_external_confirmation",
                "Внешнее действие требует отдельного подтверждения.", 409
            )
        if accept:
            self.current(p["identity"])
            guards = p["guards"]
            changes = [Mutation(e["key"], e["value"], e["expected_revision"]) for e in p["effects"]]
        p["status"] = "applied" if accept else "rejected"
        # The immutable, validated plan and its receipt change with all affected JSON
        # documents in one storage transaction, preventing replay after a crash.
        self.store.transaction([*changes, Mutation(key, preview, expected)], guards=guards)
        return self.store.get(key)

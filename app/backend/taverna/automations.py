"""Local, bounded automation rules: scheduled plugin plans, never implicit OS effects.

Rules are owner-created and tied to the exact approved plugin revision/grants.
A scheduled trigger creates a *reviewable* plan through the existing queue.
Neither JSON mutations nor external commands are applied by the scheduler.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Header
from pydantic import Field, JsonValue, model_validator

from .core import AppError
from .plugin_actions import PluginRun
from .storage import Mutation, document
from .world_models import Strict

LOG = logging.getLogger("taverna.automations")


class RuleInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    plugin_id: UUID
    trigger: Literal["manual", "interval"] = "manual"
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=50)
    max_runs: int = Field(default=100, ge=1, le=10000)
    confirmed: bool = False

    @model_validator(mode="after")
    def check_interval(self):
        if (self.trigger == "interval") != (self.interval_minutes is not None):
            raise ValueError("Интервал требуется только для запуска по расписанию.")
        return self


class RuleToggle(Strict):
    expected_revision: int = Field(ge=1)
    enabled: bool
    confirmed: bool = False


class RuleFire(Strict):
    confirmed: bool = False


class RulePayload(Strict):
    name: str
    plugin_id: str
    identity: dict[str, JsonValue]
    trigger: Literal["manual", "interval"]
    interval_minutes: int | None
    parameters: dict[str, JsonValue]
    enabled: bool
    next_due_at: str | None
    max_runs: int
    runs_started: int
    last_event_id: str | None = None


class RuleEvent(Strict):
    rule_id: str
    trigger: Literal["manual", "interval"]
    scheduled_at: str
    state: Literal["reserved", "enqueued", "failed", "interrupted"]
    job_id: str | None = None
    error_code: str | None = None


class AutomationService:
    """Owner rules and at-most-once trigger intents using the existing queue."""

    def __init__(self, plugins):
        self.plugins = plugins
        self.registry = plugins.registry
        self.store = plugins.store
        self.lock = asyncio.Lock()
        self.store.register("automation_rule", 1, RulePayload.model_json_schema())
        self.store.register("automation_event", 1, RuleEvent.model_json_schema())

    @staticmethod
    def key(rule_id):
        return f"automations/rules/{UUID(str(rule_id))}.json"

    @staticmethod
    def event_key(rule_id, event_id):
        return f"automations/runs/{UUID(str(rule_id))}/{UUID(str(event_id))}.json"

    @staticmethod
    def utc(now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Нужен часовой пояс для времени расписания.")
        return now.astimezone(timezone.utc)

    def list(self):
        rows, errors = self.store.scan("automations/rules/", tolerate_corrupt=True)
        if errors:
            raise AppError("automation_corrupt", "Часть правил повреждена. Проверьте хранилище.", 503)
        return {"items": [r["value"] for r in rows]}

    def get(self, rule_id):
        value = self.store.get(self.key(rule_id))
        if not value or value["kind"] != "automation_rule":
            raise AppError("automation_missing", "Правило не найдено.", 404)
        return value

    def create(self, body: RuleInput):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите создание правила.", 422)
        pid = str(body.plugin_id)
        row = self.registry.get(pid)
        if not row["payload"]["enabled"]:
            raise AppError("plugin_disabled", "Включите расширение перед созданием правила.", 409)
        if row["payload"]["manifest"]["runtime"] == "trusted_python":
            raise AppError(
                "automation_trust_denied",
                "Фоновый запуск Python с полным доступом запрещён. Используйте ручной запуск.",
                403,
            )
        self.plugins.parameters(row["payload"]["manifest"], body.parameters)
        identity = {
            "catalog_revision": self.registry.list()["catalog_revision"],
            "plugin_revision": row["revision"],
            "permission_epoch": row["payload"]["permission_epoch"],
            "package_sha256": row["payload"]["package_sha256"],
        }
        current = self.utc()
        payload = RulePayload(
            name=body.name,
            plugin_id=pid,
            identity=identity,
            trigger=body.trigger,
            interval_minutes=body.interval_minutes,
            parameters=body.parameters,
            enabled=True,
            next_due_at=(
                (current + timedelta(minutes=body.interval_minutes)).isoformat()
                if body.interval_minutes is not None
                else None
            ),
            max_runs=body.max_runs,
            runs_started=0,
        )
        item = document("automation_rule", payload.model_dump(mode="json"))
        self.store.transaction([Mutation(self.key(item["id"]), item, 0)])
        return self.get(item["id"])

    def toggle(self, rule_id, body: RuleToggle):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите изменение правила.", 422)
        rule = self.get(rule_id)
        if rule["revision"] != body.expected_revision:
            raise AppError("automation_conflict", "Правило изменилось. Перечитайте его.", 409)
        p = rule["payload"]
        if body.enabled and p["runs_started"] >= p["max_runs"]:
            raise AppError("automation_limit", "Достигнут предел запусков правила.", 409)
        if body.enabled:
            self._current(p)
        p["enabled"] = body.enabled
        if body.enabled and p["trigger"] == "interval":
            p["next_due_at"] = (
                self.utc() + timedelta(minutes=p["interval_minutes"])
            ).isoformat()
        self.store.transaction([Mutation(self.key(rule_id), rule, body.expected_revision)])
        return self.get(rule_id)

    def _current(self, payload):
        identity = payload["identity"]
        row = self.registry.require_current(
            payload["plugin_id"],
            identity["plugin_revision"],
            identity["permission_epoch"],
            identity["package_sha256"],
            catalog_revision=identity["catalog_revision"],
        )
        if row["payload"]["manifest"]["runtime"] == "trusted_python":
            raise AppError("automation_trust_denied", "Фоновый Python запрещён.", 403)
        self.plugins.parameters(row["payload"]["manifest"], payload["parameters"])
        return row

    def _has_unreviewed_plan(self, rule):
        eid = rule["payload"]["last_event_id"]
        if not eid:
            return False
        event = self.store.get(self.event_key(rule["id"], eid))
        if not event or event["payload"]["state"] == "reserved":
            return True
        jid = event["payload"]["job_id"]
        if not jid:
            return False
        job = self.store.get(self.plugins.queue.key(jid))
        if not job:
            return True
        if job["payload"]["state"] in ("queued", "running"):
            return True
        if job["payload"]["state"] == "completed":
            result = job["payload"].get("result") or {}
            pid = result.get("proposal_id")
            if pid:
                preview = self.store.get(self.plugins.proposal_key(pid))
                return bool(preview and preview["payload"]["status"] == "pending")
        return False

    def _reserve(self, rule, now, trigger, event_id):
        key = self.event_key(rule["id"], event_id)
        existing = self.store.get(key)
        if existing:
            return existing
        p = rule["payload"]
        if not p["enabled"] or p["runs_started"] >= p["max_runs"]:
            raise AppError("automation_disabled", "Правило отключено или достигнут предел запусков.", 409)
        self._current(p)
        event = document(
            "automation_event",
            RuleEvent(
                rule_id=rule["id"],
                trigger=trigger,
                scheduled_at=now.isoformat(),
                state="reserved",
            ).model_dump(mode="json"),
            entity_id=str(event_id),
        )
        p["runs_started"] += 1
        p["last_event_id"] = event["id"]
        if p["trigger"] == "interval":
            p["next_due_at"] = (
                now + timedelta(minutes=p["interval_minutes"])
            ).isoformat()
        self.store.transaction(
            [
                Mutation(key, event, 0),
                Mutation(self.key(rule["id"]), rule, rule["revision"]),
            ]
        )
        return self.store.get(key)

    async def _dispatch(self, event):
        p = event["payload"]
        if p["state"] != "reserved":
            return event
        rule = self.get(p["rule_id"])
        identity = rule["payload"]["identity"]
        body = PluginRun(
            **identity,
            parameters=rule["payload"]["parameters"],
            reads=[],
            files=[],
            confirmed_full_access_run=False,
        )
        try:
            # A durable server-owned key prevents duplicate planning jobs
            # after a failed HTTP response or application restart.
            job = await self.plugins.enqueue(
                rule["payload"]["plugin_id"],
                body,
                f"automation-plan/{rule['id']}/{event['id']}",
            )
            p["job_id"] = job["id"]
            p["state"] = (
                "interrupted" if job["payload"]["state"] == "interrupted" else "enqueued"
            )
        except AppError as exc:
            p["state"] = "failed"
            p["error_code"] = exc.code
        self.store.transaction(
            [Mutation(self.event_key(rule["id"], event["id"]), event, event["revision"])]
        )
        return self.store.get(self.event_key(rule["id"], event["id"]))

    async def fire(self, rule_id, *, confirmed=False, token=None, now=None):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите запуск правила.", 422)
        now = self.utc(now)
        async with self.lock:
            rule = self.get(rule_id)
            if token is None or not 1 <= len(token) <= 128:
                raise AppError("invalid_idempotency_key", "Укажите ключ повторного запроса.", 422)
            # Stable token prevents different HTTP retries reserving a second event.
            event_id = uuid5(NAMESPACE_URL, f"automation:{rule['id']}:{token}")
            event = self._reserve(rule, now, "manual", event_id)
            return await self._dispatch(event)

    async def recover(self):
        """Recover reserved plan intents only; never replay interrupted jobs."""
        async with self.lock:
            rows, errors = await asyncio.to_thread(
                self.store.scan, "automations/runs/", tolerate_corrupt=True
            )
            if errors:
                LOG.warning("Повреждены данные автоматизации; восстановление требует проверки.")
                return
            for row in rows:
                if row["value"]["payload"]["state"] == "reserved":
                    try:
                        await self._dispatch(row["value"])
                    except Exception as exc:
                        LOG.warning("Не удалось восстановить ожидающий план: %s", type(exc).__name__)

    async def tick(self, now=None):
        now = self.utc(now)
        async with self.lock:
            rules = await asyncio.to_thread(self.list)
            for rule in rules["items"]:
                p = rule["payload"]
                if (
                    not p["enabled"]
                    or p["trigger"] != "interval"
                    or p["runs_started"] >= p["max_runs"]
                    or datetime.fromisoformat(p["next_due_at"]) > now
                    or self._has_unreviewed_plan(rule)
                ):
                    continue
                try:
                    event = await asyncio.to_thread(
                        self._reserve, rule, now, "interval", uuid4()
                    )
                    await self._dispatch(event)
                except (AppError, OSError) as exc:
                    LOG.warning("Автоматизация не запущена: %s", getattr(exc, "code", type(exc).__name__))

    async def loop(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Ошибка проверки правил: %s", type(exc).__name__)
            await asyncio.sleep(60)

    def history(self, rule_id):
        self.get(rule_id)
        rows, errors = self.store.scan(
            f"automations/runs/{UUID(str(rule_id))}/", tolerate_corrupt=True
        )
        if errors:
            raise AppError("automation_corrupt", "Повреждены записи запусков.", 503)
        items = []
        for row in sorted(rows, key=lambda x: x["value"]["created_at"], reverse=True)[:100]:
            event = row["value"]
            jid = event["payload"]["job_id"]
            job = self.store.get(self.plugins.queue.key(jid)) if jid else None
            proposal_id = (
                (job["payload"].get("result") or {}).get("proposal_id")
                if job and job["payload"]["state"] == "completed"
                else None
            )
            items.append({
                **event,
                "job_state": job["payload"]["state"] if job else None,
                "proposal_id": proposal_id,
            })
        return {"items": items}

    def router(self):
        api = APIRouter(prefix="/api/v1/automations")

        @api.get("")
        def list_rules():
            return self.list()

        @api.post("")
        def create(body: RuleInput):
            return self.create(body)

        @api.get("/{rule_id}")
        def get(rule_id: UUID):
            return self.get(rule_id)

        @api.patch("/{rule_id}")
        def update(rule_id: UUID, body: RuleToggle):
            return self.toggle(rule_id, body)

        @api.post("/{rule_id}/run")
        async def run(
            rule_id: UUID,
            body: RuleFire,
            idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        ):
            return await self.fire(rule_id, confirmed=body.confirmed, token=idempotency_key)

        @api.get("/{rule_id}/history")
        def history(rule_id: UUID):
            return self.history(rule_id)

        return api

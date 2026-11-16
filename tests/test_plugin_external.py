"""Real executable command approvals: durable queue, explicit scope, and no replay."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from taverna.core import AppError, Config
from taverna.main import create_app
from taverna.plugin_actions import PluginCommandDecision, PluginCommandReconcile, PluginRun
from taverna.storage import Mutation
from test_plugin_packages import package
from test_plugin_registry import configure, install

EXECUTABLE = Path(sys.executable).resolve()
CHECKSUM = hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest()


def install_command(tmp_path, code, *, allowed=True, timeout=4):
    app = create_app(Config(tmp_path))
    service = app.state.plugins
    action = {
        "type": "command.exec",
        "executable": str(EXECUTABLE),
        "sha256": CHECKSUM,
        "argv": ["-c", code],
        "timeout_seconds": timeout,
    }
    plan = {"format": 1, "actions": [action], "message": "Запуск по подтверждению"}
    raw = package(
        {
            "runtime": "declarative",
            "entry": "main.json",
            "requests": [
                {"capability": "commands.exec", "reason": "Ручной запуск файла", "scopes": [str(EXECUTABLE)]}
            ],
        },
        json.dumps(plan, ensure_ascii=False).encode("utf-8"),
    )
    row = install(service.registry, raw)
    grants = (
        [{"capability": "commands.exec", "scopes": [str(EXECUTABLE)], "full_access_warning_ack": True}]
        if allowed
        else []
    )
    row = configure(service.registry, row, grants=grants)
    return service, row


async def finished(service, job):
    async with asyncio.timeout(15):
        while True:
            current = await service.queue.get(job["id"])
            if current["payload"]["state"] not in ("queued", "running"):
                return current
            await asyncio.sleep(0.02)


async def preview_command(service, row):
    body = PluginRun(
        catalog_revision=service.registry.list()["catalog_revision"],
        plugin_revision=row["revision"],
        permission_epoch=row["payload"]["permission_epoch"],
        package_sha256=row["payload"]["package_sha256"],
    )
    job = await service.enqueue(row["id"], body, str(uuid4()))
    result = await finished(service, job)
    if result["payload"]["state"] != "completed":
        return result
    proposal_id = result["payload"]["result"]["proposal_id"]
    return service.store.get(service.proposal_key(proposal_id))


def approval(preview, **changes):
    command = preview["payload"]["metadata"]["external_command"]
    return PluginCommandDecision(
        expected_revision=preview["revision"],
        confirmed=True,
        full_access_warning_ack=True,
        fingerprint=command["fingerprint"],
        **changes,
    )


@pytest.mark.asyncio
async def test_command_needs_separate_owner_confirm_and_never_replays(tmp_path):
    marker = tmp_path / "executed-counter.txt"
    script = (
        "from pathlib import Path; "
        f"p=Path({str(marker)!r});"
        "p.write_text((p.read_text() if p.exists() else '')+'X')"
    )
    service, row = install_command(tmp_path, script)
    try:
        preview = await preview_command(service, row)
        assert preview["payload"]["status"] == "pending"
        assert not marker.exists()
        with pytest.raises(AppError, match="отдельного подтверждения"):
            service.decide(preview["id"], preview["revision"], True)
        body = approval(preview)
        with pytest.raises(AppError, match="Подтвердите"):
            await service.approve_external(
                preview["id"], body.model_copy(update={"full_access_warning_ack": False})
            )
        assert not marker.exists()
        one = await service.approve_external(preview["id"], body)
        two = await service.approve_external(preview["id"], body)
        assert one["id"] == two["id"]
        result = await finished(service, one)
        assert result["payload"]["state"] == "completed", result["payload"]
        assert marker.read_text() == "X"
        observed = service.store.get(service.proposal_key(preview["id"]))
        assert observed["payload"]["status"] == "observed"
        assert observed["payload"]["external_job_id"] == one["id"]
        assert observed["payload"]["observed"]["return_code"] == 0
        # Already-observed job is still idempotent after re-opening the store.
        reopened = create_app(Config(tmp_path)).state.plugins
        again = await reopened.approve_external(preview["id"], body)
        assert again["id"] == one["id"]
        assert marker.read_text() == "X"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_grant_required_and_revocation_before_approval_prevents_launch(tmp_path):
    marker = tmp_path / "forbidden.txt"
    script = f"from pathlib import Path;Path({str(marker)!r}).write_text('no')"
    service, row = install_command(tmp_path, script, allowed=False)
    try:
        result = await preview_command(service, row)
        assert result["payload"]["state"] == "failed"
        assert result["payload"]["error"]["code"] == "plugin_scope_denied"
        assert not marker.exists()
    finally:
        await service.queue.close()

    service, row = install_command(tmp_path / "other", script, allowed=True)
    try:
        preview = await preview_command(service, row)
        row = configure(service.registry, row, grants=[])
        with pytest.raises(AppError):
            await service.approve_external(preview["id"], approval(preview))
        assert not marker.exists()
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_timeout_remains_unknown_and_manual_reconcile_does_not_reexecute(tmp_path):
    marker = tmp_path / "partial-effect.txt"
    script = (
        "from pathlib import Path; import time;"
        f"Path({str(marker)!r}).write_text('effect');time.sleep(30)"
    )
    service, row = install_command(tmp_path, script, timeout=1)
    try:
        preview = await preview_command(service, row)
        job = await service.approve_external(preview["id"], approval(preview))
        result = await finished(service, job)
        assert result["payload"]["state"] == "completed"
        updated = service.store.get(service.proposal_key(preview["id"]))
        assert updated["payload"]["status"] == "unknown"
        assert marker.read_text() == "effect"
        with pytest.raises(AppError):
            service.decide(preview["id"], updated["revision"], True)
        reconciled = service.reconcile_external(
            preview["id"],
            PluginCommandReconcile(
                expected_revision=updated["revision"],
                confirmed=True,
                outcome="unknown",
                reason="Проверено вручную; внешние изменения возможны",
            ),
        )
        assert reconciled["payload"]["status"] == "reconciled"
        again = await service.approve_external(preview["id"], approval(preview))
        assert again["id"] == job["id"]
        assert marker.read_text() == "effect"
    finally:
        await service.queue.close()


def test_recover_started_does_not_run_command(tmp_path):
    marker = tmp_path / "never-started"
    service, row = install_command(
        tmp_path, f"from pathlib import Path;Path({str(marker)!r}).touch()"
    )
    from taverna.storage import document

    command = {
        "type": "command.exec",
        "executable": str(EXECUTABLE),
        "sha256": CHECKSUM,
        "argv": [],
        "timeout_seconds": 1,
    }
    command["fingerprint"] = hashlib.sha256(
        __import__("taverna.storage", fromlist=["encoded"]).encoded(command)
    ).hexdigest()
    preview = document(
        "plugin_preview",
        {
            "status": "started",
            "metadata": {"external_command": command},
            "external_job_id": str(uuid4()),
            "identity": {"plugin_id": row["id"]},
            "plan": {"format": 1, "actions": [], "message": ""},
            "effects": [],
            "guards": {},
        },
    )
    key = service.proposal_key(preview["id"])
    service.store.transaction([Mutation(key, preview, 0)])
    assert service.recover_external() == 1
    recovered = service.store.get(key)
    assert recovered["payload"]["status"] == "unknown"
    assert service.recover_external() == 0
    assert not marker.exists()

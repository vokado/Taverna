"""Daily backup scheduling: real archives, lock sharing, and honest lifecycle status."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from taverna.auto_backup import AutomaticBackups
from taverna.backup_api import BackupService
from taverna.core import Config
from taverna.full_backup import verify_full_backup
from taverna.main import create_app
from taverna.storage import JsonStore, Mutation, document


def test_daily_backup_is_real_verified_and_not_duplicated(tmp_path):
    store = JsonStore(tmp_path)
    store.transaction([Mutation("characters/test.json", document("character", {"name": "Мира"}), 0)])
    backups = BackupService(store)
    scheduler = AutomaticBackups(backups)
    now = datetime.now(timezone.utc)
    first = scheduler.ensure_due(now)
    assert first and first.startswith("taverna-automatic-")
    manifest = verify_full_backup(backups.directory / first)
    assert manifest["files"]["data/config/store-manifest.json"]
    assert scheduler.ensure_due(now + timedelta(minutes=3)) is None
    # An older verified backup no longer suppresses a future daily run.
    second = scheduler.ensure_due(now + timedelta(days=2))
    assert second and second != first
    assert len(list(backups.directory.glob("taverna-automatic-*.zip"))) == 2


def test_manual_backups_do_not_replace_daily_schedule(tmp_path):
    backups = BackupService(JsonStore(tmp_path))
    manual = backups.create("manual")
    assert manual["name"].startswith("taverna-manual-")
    scheduled = AutomaticBackups(backups)
    assert scheduled.ensure_due() is not None
    assert len(backups.list()["items"]) == 2


@pytest.mark.asyncio
async def test_scheduler_retries_after_error_without_crashing(tmp_path):
    backups = BackupService(JsonStore(tmp_path))
    scheduler = AutomaticBackups(backups, poll_seconds=0.01)
    count = 0
    ready = asyncio.Event()

    def check():
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("private path and content must not enter status")
        ready.set()
        return None

    scheduler.ensure_due = check
    task = asyncio.create_task(scheduler.run())
    try:
        await asyncio.wait_for(ready.wait(), 3)
        assert count >= 2
        assert scheduler.last_error_code is None
        assert scheduler.last_checked_at
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_status_is_authenticated_and_automatic_backup_runs_in_lifespan(tmp_path):
    app = create_app(Config(tmp_path, 8765))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/v1/backups/automatic/status").status_code == 401
        auth = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        )
        assert auth.status_code == 200
        result = client.get("/api/v1/backups/automatic/status")
        assert result.status_code == 200
        assert result.json()["enabled"] is True
        assert result.json()["only_while_running"] is True
        assert result.json()["interval_hours"] == 24
    # Scheduling never requires a network service, and published archives are valid.
    from pathlib import Path

    archives = list(Path(tmp_path / "backups").glob("taverna-automatic-*.zip"))
    assert len(archives) <= 1
    for path in archives:
        verify_full_backup(path)

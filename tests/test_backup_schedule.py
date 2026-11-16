"""Daily backup scheduling and retention: real files, no clock sleeps."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from taverna.backup_api import BackupService
from taverna.core import Config
from taverna.full_backup import verify_full_backup
from taverna.main import create_app
from taverna.storage import JsonStore, Mutation, document


def test_daily_backup_survives_service_restart_and_uses_valid_archive(tmp_path):
    store = JsonStore(tmp_path)
    store.transaction([Mutation("characters/one.json", document("test", {"name": "Мира"}), 0)])
    service = BackupService(store)
    first = service.ensure_automatic()
    assert first["state"] == "created"
    archive = tmp_path / "backups" / first["name"]
    assert verify_full_backup(archive)["files"]
    assert service.ensure_automatic()["state"] == "not_due"
    restarted = BackupService(JsonStore(tmp_path))
    assert restarted.ensure_automatic()["state"] == "not_due"
    assert len(list((tmp_path / "backups").glob("taverna-automatic-*.zip"))) == 1


def test_retention_removes_only_old_automatic_archives(tmp_path):
    store = JsonStore(tmp_path)
    service = BackupService(store)
    now = datetime.now(timezone.utc)
    copies = []
    for days in range(1, 85, 3):
        stamp = (now - timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ")
        path = tmp_path / "backups" / f"taverna-automatic-{stamp}-{'a' * 31}{days % 10}.zip"
        path.write_bytes(b"old-verified-by-retention-policy-not-by-test")
        copies.append(path)
    manual = tmp_path / "backups" / f"taverna-manual-{now.strftime('%Y%m%dT%H%M%SZ')}-{'f' * 32}.zip"
    manual.write_bytes(b"must-not-delete")
    created = service.ensure_automatic(now=now)
    assert created["state"] == "created"
    automatic = list((tmp_path / "backups").glob("taverna-automatic-*.zip"))
    assert len(automatic) <= 11
    assert len(automatic) >= 7
    assert manual.read_bytes() == b"must-not-delete"
    assert (tmp_path / "backups" / created["name"]).exists()
    assert sum(path.exists() for path in copies) < len(copies)


def test_automatic_error_does_not_suppress_next_attempt(tmp_path):
    store = JsonStore(tmp_path)
    service = BackupService(store)
    missing = document(
        "media_asset",
        {"relative_path": "media/assets/" + "a" * 64 + ".png", "checksum": "a" * 64},
    )
    store.transaction([Mutation("media/missing.json", missing, 0)])
    try:
        service.ensure_automatic()
    except Exception as exc:
        assert getattr(exc, "code", None) == "backup_asset_missing"
    else:
        raise AssertionError("Missing asset must prevent backup publication")
    assert not list((tmp_path / "backups").glob("taverna-automatic-*.zip"))
    store.transaction([Mutation("media/missing.json", None, 1)])
    assert service.ensure_automatic()["state"] == "created"


def test_lifespan_runs_auto_backup_when_application_is_open(tmp_path):
    app = create_app(Config(tmp_path, 8765))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/v1/health").status_code == 200
        # Scheduler runs in a worker thread and may publish after health is served.
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if list((tmp_path / "backups").glob("taverna-automatic-*.zip")):
                break
            time.sleep(0.05)
        assert list((tmp_path / "backups").glob("taverna-automatic-*.zip"))
    assert app.state.generation.queue.closed

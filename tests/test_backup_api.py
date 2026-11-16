from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
from taverna.core import Config
from taverna.main import create_app
from taverna.storage import Mutation, document


def test_backup_api_requires_session_and_csrf_and_downloads_verified_archive(tmp_path):
    app = create_app(Config(tmp_path, 8765))
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    store = app.state.store
    store.transaction([Mutation("characters/sample.json", document("test", {"name": "Мира"}), 0)])
    assert client.get("/api/v1/backups").status_code == 401
    assert client.post("/api/v1/backups", json={"reason": "manual"}).status_code == 401

    auth = client.post(
        "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
    )
    assert auth.status_code == 200
    assert client.post("/api/v1/backups", json={"reason": "manual"}).status_code == 403
    headers = {"X-Taverna-CSRF": auth.json()["csrf"]}
    created = client.post("/api/v1/backups", json={"reason": "manual"}, headers=headers)
    assert created.status_code == 200, created.text
    name = created.json()["name"]
    assert name.startswith("taverna-manual-")
    listed = client.get("/api/v1/backups")
    assert listed.status_code == 200
    assert name in {entry["name"] for entry in listed.json()["items"]}

    verified = client.post(f"/api/v1/backups/{name}/verify", headers=headers)
    assert verified.status_code == 200
    assert verified.json()["verified"] and verified.json()["files"] >= 2

    download = client.get(f"/api/v1/backups/{name}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(download.content)) as archive:
        assert "backup-manifest.json" in archive.namelist()
        assert "data/config/store-manifest.json" in archive.namelist()
    assert client.get("/api/v1/backups/not-an-archive.zip/download").status_code == 422
    assert client.post(
        "/api/v1/backups", json={"reason": "../unsafe"}, headers=headers
    ).status_code == 422

"""Diagnostic ZIP is local, private by default and never includes raw histories."""

import json
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
from taverna.core import Config
from taverna.main import create_app
from taverna.storage import Mutation, document

PRIVATE = "PRIVATE-STORY-MARKER-ONLY-FOR-THIS-TEST"


def test_default_diagnostics_exclude_personal_content_and_require_session(tmp_path):
    app = create_app(Config(tmp_path, 8765))
    app.state.store.transaction([
        Mutation("chats/private.json", document("private", {"text": PRIVATE}), 0)
    ])
    (tmp_path / "logs" / "taverna.log").write_text(
        "SECRET-RAW-LOG-ONLY", encoding="utf-8"
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.get("/api/v1/diagnostics/archive").status_code == 401
    auth = client.post(
        "/api/v1/session/bootstrap",
        json={"token": app.state.sessions.bootstrap()},
    )
    assert auth.status_code == 200
    response = client.get("/api/v1/diagnostics/archive")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert "Taverna-Diagnostics-" in response.headers["content-disposition"]
    assert PRIVATE.encode() not in response.content
    assert b"SECRET-RAW-LOG-ONLY" not in response.content
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["report.json", "README.txt"]
        report = json.loads(archive.read("report.json"))
        assert report["private_content_included"] is False
        assert report["logs_included"] is False
        assert report["data"]["integrity_ok"] is True
        assert report["data"]["document_count"] >= 1
        assert "private.json" not in str(report)
        assert PRIVATE not in str(report)


def test_optional_logs_are_bounded_anonymized_and_explicit(tmp_path):
    app = create_app(Config(tmp_path, 8765))
    (tmp_path / "logs" / "taverna.log").write_text(
        "token=do-not-leak\n"
        "email=somebody@example.com ip=192.168.1.10\n"
        "path=C:\\Users\\PrivatePerson\\Documents\n"
        + ("x" * 200_000),
        encoding="utf-8",
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.post(
        "/api/v1/session/bootstrap",
        json={"token": app.state.sessions.bootstrap()},
    ).status_code == 200
    response = client.get("/api/v1/diagnostics/archive?include_logs=true")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["report.json", "README.txt", "logs/journal-1.txt"]
        content = archive.read("logs/journal-1.txt").decode("utf-8")
        assert "do-not-leak" not in content
        assert "somebody@example.com" not in content
        assert "192.168.1.10" not in content
        assert "PrivatePerson" not in content
        assert "[СКРЫТО]" in content
        assert "[EMAIL]" in content
        assert "[IP]" in content
        assert len(content) <= 140_000
        report = json.loads(archive.read("report.json"))
        assert report["logs_included"] is True
        assert report["private_content_included"] is False

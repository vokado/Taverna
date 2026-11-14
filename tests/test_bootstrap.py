from pathlib import Path

from fastapi.testclient import TestClient
from taverna.core import Config
from taverna.main import create_app


def client(tmp_path: Path, max_body=1024):
    app = create_app(Config(tmp_path, 8765, max_body))
    return app, TestClient(app, base_url="http://127.0.0.1:8765")


def login(app, c):
    result = c.post("/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()})
    assert result.status_code == 200
    return {"X-Taverna-CSRF": result.json()["csrf"]}


def test_session_host_csrf_and_one_use(tmp_path):
    app, c = client(tmp_path)
    assert c.get("/api/v1/status").status_code == 401
    token = app.state.sessions.bootstrap()
    response = c.post("/api/v1/session/bootstrap", json={"token": token})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert c.post("/api/v1/session/bootstrap", json={"token": token}).status_code == 401
    assert c.get("/api/v1/status").status_code == 200
    assert c.get("/api/v1/status", headers={"Host": "evil.test:8765"}).status_code == 403
    assert c.get("/api/v1/status", headers={"Origin": "https://evil.test"}).status_code == 403
    assert c.post("/api/v1/system/shutdown").status_code == 403
    assert (
        c.post("/api/v1/system/shutdown", headers={"X-Taverna-CSRF": response.json()["csrf"]}).status_code
        == 409
    )


def test_limits_and_localized_errors(tmp_path):
    app, c = client(tmp_path)
    headers = login(app, c)
    assert c.post("/api/v1/system/shutdown", content=b"a" * 2048, headers=headers).status_code == 413
    invalid = c.post("/api/v1/session/bootstrap", json={"token": "short", "extra": "secret"})
    assert invalid.status_code == 422
    assert "secret" not in invalid.text
    assert invalid.json()["message_ru"]
    assert c.get("/api/v1/missing").json()["message_ru"] == "Ресурс не найден."
    assert "frame-ancestors 'none'" in c.get("/").headers["content-security-policy"]


def test_missing_frontend_and_shutdown(tmp_path):
    calls = []
    app = create_app(Config(tmp_path), lambda: calls.append(True))
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    headers = login(app, c)
    assert c.get("/").status_code == 503
    assert c.post("/api/v1/system/shutdown", headers=headers).status_code == 200
    assert c.get("/api/v1/capabilities").json()["features"]["graceful_shutdown"]

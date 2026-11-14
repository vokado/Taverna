from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from taverna.cards import new_card
from taverna.core import Config
from taverna.main import create_app


def test_raw_invalid_json_draft_survives_restart_without_publishing(tmp_path: Path):
    identifier = str(uuid4())
    payload = {
        "expected_revision": 0,
        "title": "Незавершённая карточка",
        "character": new_card(),
        "raw_text": '{"незавершённый JSON":',
        "source_id": None,
        "source_revision": 0,
        "raw_applied": False,
        "editor": "expert",
    }
    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        headers = {"X-Taverna-CSRF": csrf}
        result = client.put(f"/api/v1/characters/drafts/{identifier}", json=payload, headers=headers)
        assert result.status_code == 200 and result.json()["revision"] == 1
        assert client.get("/api/v1/characters").json()["characters"] == []
        assert (
            client.put(f"/api/v1/characters/drafts/{identifier}", json=payload, headers=headers).status_code
            == 409
        )
    restarted = create_app(Config(tmp_path))
    with TestClient(restarted, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": restarted.state.sessions.bootstrap()}
        ).json()["csrf"]
        recovered = client.get("/api/v1/characters/drafts").json()["drafts"]
        assert recovered[0]["payload"]["raw_text"] == payload["raw_text"]
        assert recovered[0]["payload"]["raw_applied"] is False
        assert (
            client.request(
                "DELETE",
                f"/api/v1/characters/drafts/{identifier}",
                json={"expected_revision": 1},
                headers={"X-Taverna-CSRF": csrf},
            ).status_code
            == 200
        )

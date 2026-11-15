import copy
import io
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from taverna.core import AppError
from taverna.media import MediaService
from taverna.media_models import Composition, MediaState
from taverna.media_probe import probe_media
from taverna.storage import safe_path
from taverna.worlds import WorldService
from test_chat import append, setup_chat


def picture(fmt="PNG", animated=False):
    out = io.BytesIO()
    image = Image.new("RGB", (24, 32), "red")
    if animated:
        image.save(
            out,
            format=fmt,
            save_all=True,
            append_images=[Image.new("RGB", (24, 32), "blue")],
            duration=100,
            loop=0,
        )
    else:
        image.save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize(
    "fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")]
)
def test_image_probe_actual_content_and_frames(fmt, mime):
    info = probe_media(picture(fmt))
    assert info["type"] == mime and (info["width"], info["height"]) == (24, 32)
    assert info["frames"] == 1
    if fmt != "JPEG":
        info = probe_media(picture(fmt, True))
        assert info["frames"] == 2 and info["duration_seconds"] == 0.2


@pytest.mark.parametrize(
    "extension,mime",
    [("wav", "audio/wav"), ("mp3", "audio/mpeg"), ("ogg", "audio/ogg"), ("flac", "audio/flac")],
)
def test_audio_probe_committed_synthetic_fixtures(extension, mime):
    raw = (Path(__file__).parent / "fixtures/media" / ("tone." + extension)).read_bytes()
    info = probe_media(raw)
    assert info["type"] == mime and 0.2 < info["duration_seconds"] < 0.5
    assert info["channels"] == 1 and info["sample_rate"] == 44100


def test_invalid_oversize_active_content_and_bounded_animation(monkeypatch):
    from taverna import media_probe

    for bad in [
        b'<svg onload="alert(1)"></svg>',
        b"<html>active</html>",
        b"ID3bad",
        b"RIFFbad",
        picture()[:40],
        b"",
    ]:
        with pytest.raises(AppError):
            probe_media(bad)
    monkeypatch.setattr(media_probe, "MAX_IMAGE_PIXELS", 24 * 32 - 1)
    with pytest.raises(AppError, match="предел"):
        probe_media(picture())
    monkeypatch.setattr(media_probe, "MAX_IMAGE_PIXELS", 1000)
    monkeypatch.setattr(media_probe, "MAX_FRAMES", 1)
    with pytest.raises(AppError, match="Анимация"):
        probe_media(picture("GIF", True))
    raw = (Path(__file__).parent / "fixtures/media/tone.wav").read_bytes()
    with pytest.raises(AppError):
        probe_media(raw[:-10])


def test_media_import_search_archive_cas_checksum_and_existing_png(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    service = MediaService(WorldService(chat))
    value = service.import_raw(picture(), "Гавань", "background", ["море", "ночь"])
    aid = value["id"]
    assert service.list("ГАВАНЬ", tag="море")["items"][0]["id"] == aid
    assert service.checked_file(aid)[1].is_file()
    with pytest.raises(AppError):
        service.update(aid, 0, {"name": "Конфликт"})
    archived = service.update(aid, value["revision"], {"archived": True})
    assert not service.list()["items"] and service.list(archived=True)["items"]
    assert service.checked_file(aid)[0]["payload"]["archived"]
    before = service.get(aid)
    with pytest.raises(AppError):
        service.update(aid, archived["revision"], {"category": "music"})
    assert service.get(aid) == before
    path = safe_path(tmp_path, value["payload"]["relative_path"])
    path.write_bytes(b"broken")
    with pytest.raises(AppError, match="повреждён"):
        service.checked_file(aid)


def test_scene_layers_variant_emotion_scope_audio_and_fork(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    service = MediaService(WorldService(chat))
    base = service.import_raw(picture(), "Портрет", "portrait")
    smile = service.import_raw(picture("WEBP"), "Улыбка", "expression")
    music = service.import_raw(
        (Path(__file__).parent / "fixtures/media/tone.wav").read_bytes(), "Тема", "music"
    )
    bid = chat.branch(sid)["id"]
    scene_id = chat.state(sid)["payload"]["scene"]["id"]
    media = MediaState(
        compositions={
            scene_id: Composition(
                layers=[{"name": "NPC", "asset_id": base["id"], "character_id": ids[0]}],
                music={"asset_id": music["id"], "enabled": True},
            )
        },
        variants=[{"character_id": ids[0], "asset_id": smile["id"], "emotion": "радость"}],
    ).model_dump(mode="json")
    service.save_scene(sid, bid, chat.branch(sid)["revision"], media, "Выбор сцены")
    read = service.read_scene(sid, bid)
    assert read["resolved_layers"][0]["asset_id"] == base["id"]
    from taverna.director import DirectorService
    from taverna.memory import MemoryService

    director = DirectorService(MemoryService(chat), service.worlds)
    director.write_npc(
        sid, bid, ids[0], chat.branch(sid)["revision"], {"emotion": {"label": "радость"}}, "Ручная эмоция"
    )
    assert service.read_scene(sid, bid)["resolved_layers"][0]["asset_id"] == smile["id"]
    before = chat.state(sid)
    bad = copy.deepcopy(service.read_scene(sid, bid)["media"])
    bad["compositions"][scene_id]["music"]["asset_id"] = base["id"]
    with pytest.raises(AppError, match="аудиофайл"):
        service.save_scene(sid, bid, chat.branch(sid)["revision"], bad, "Неверный тип")
    assert chat.state(sid) == before
    bad = copy.deepcopy(service.read_scene(sid, bid)["media"])
    bad["variants"][0]["character_id"] = str(uuid4())
    with pytest.raises(AppError):
        service.save_scene(sid, bid, chat.branch(sid)["revision"], bad, "Чужой NPC")
    first = append(chat, sid, "Первый эпизод")
    append(chat, sid, "Второй эпизод")
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"], through_id=first["id"])
    forked = service.read_scene(sid, chat.branch(sid)["id"])
    assert forked["media"]["compositions"][scene_id]["layers"][0]["asset_id"] == base["id"]
    assert forked["composition"]["music"]["asset_id"] == music["id"]
    assert chat.messages(sid)["items"][0]["text"] == "Первый эпизод"
    assert chat.store.integrity()["ok"]


def test_media_binary_api_real_session_metadata_and_scope(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        response = client.post(
            "/api/v1/media/import",
            params={"name": "Гавань", "category": "background", "confirmed": True},
            content=picture(),
        )
        assert response.status_code == 403
        client.headers.update({"X-Taverna-CSRF": csrf})
        response = client.post(
            "/api/v1/media/import",
            params={"name": "Гавань", "category": "background", "confirmed": True},
            content=picture(),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 200, response.text
        value = response.json()
        served = client.get("/api/v1/media/" + value["id"] + "/file")
        assert served.status_code == 200 and served.content == picture()
        assert (
            served.headers["content-type"] == "image/png"
            and served.headers["x-content-type-options"] == "nosniff"
        )
        assert client.get("/api/v1/media", params={"query": "гавань"}).json()["items"][0]["id"] == value["id"]
        assert not list(tmp_path.rglob("*.sqlite*"))


def test_story_zip_includes_animation_audio_and_removed_history_assets(tmp_path):
    from taverna.story_packages import StoryPackages

    chat, sid, ids = setup_chat(tmp_path / "source")
    service = MediaService(WorldService(chat))
    background = service.import_raw(picture("GIF", True), "Дождь", "animation")
    music = service.import_raw(
        (Path(__file__).parent / "fixtures/media/tone.mp3").read_bytes(), "Тема", "music"
    )
    scene_id = chat.state(sid)["payload"]["scene"]["id"]
    bid = chat.branch(sid)["id"]
    media = MediaState(
        compositions={
            scene_id: Composition(
                layers=[{"name": "Дождь", "asset_id": background["id"]}],
                music={"asset_id": music["id"], "enabled": True},
            )
        }
    ).model_dump(mode="json")
    service.save_scene(sid, bid, chat.branch(sid)["revision"], media, "Первая композиция")
    media = service.read_scene(sid, bid)["media"]
    media["compositions"][scene_id]["layers"] = []
    service.save_scene(sid, bid, chat.branch(sid)["revision"], media, "Убрать видимый фон, сохранить историю")
    raw = StoryPackages(chat).export(sid)
    target, _, _ = setup_chat(tmp_path / "target")
    target_media = MediaService(WorldService(target))
    packages = StoryPackages(target)
    preview = packages.preview(raw)
    assert preview["asset_count"] == 2
    packages.apply(raw, True, preview["sha256"])
    assert target_media.checked_file(background["id"])[1].read_bytes() == picture("GIF", True)
    assert (
        target_media.checked_file(music["id"])[1].read_bytes()
        == (Path(__file__).parent / "fixtures/media/tone.mp3").read_bytes()
    )
    assert len(target_media.read_scene(sid, bid)["media"]["history"]) == 2


def test_media_and_scene_drafts_keep_invalid_numbers_and_raw_without_primary_write(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.cards import new_card
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf})
        aid = client.post(
            "/api/v1/media/import",
            params={"name": "Портрет", "category": "portrait", "confirmed": True},
            content=picture(),
        ).json()["id"]
        asset = app.state.media.get(aid)
        draft_id = str(uuid4())
        library = {
            "template_id": aid,
            "source_revision": asset["revision"],
            "expected_revision": 0,
            "raw": "{незавершено",
            "payload": {"name": "", "category": "portrait"},
        }
        path = "/api/v1/media/drafts/" + draft_id
        assert client.put(path, json=library).status_code == 200
        assert client.put(path, json=library).status_code == 409
        assert client.post("/api/v1/media/drafts/validate", json=library["payload"]).status_code == 422
        assert app.state.media.get(aid) == asset
        actor = client.post(
            "/api/v1/characters", json={"expected_revision": 0, "character": new_card("Мира")}
        ).json()["id"]
        sid = client.post("/api/v1/stories", json={"name": "Сцена", "character_ids": [actor]}).json()["id"]
        chat = app.state.chat
        bid = chat.branch(sid)["id"]
        scene_id = chat.state(sid)["payload"]["scene"]["id"]
        media = MediaState(
            compositions={scene_id: Composition(layers=[{"name": "NPC", "asset_id": aid}])}
        ).model_dump(mode="json")
        media["compositions"][scene_id]["layers"][0]["opacity"] = 2
        before = chat.state(sid)
        base = f"/api/v1/stories/{sid}/media"
        body = {
            "template_id": None,
            "source_revision": chat.branch(sid)["revision"],
            "expected_revision": 0,
            "raw": "{bad scene",
            "payload": {"media": media, "reason": "Не закончено"},
        }
        path = base + f"/drafts/{bid}/{uuid4()}"
        result = client.put(path, json=body)
        assert result.status_code == 200, result.text
        assert (
            result.json()["payload"]["payload"]["media"]["compositions"][scene_id]["layers"][0]["opacity"]
            == 2
        )
        assert client.post(base + f"/drafts/{bid}/validate", json=media).status_code == 422
        assert (
            client.put(
                base,
                json={
                    "branch_id": bid,
                    "expected_revision": chat.branch(sid)["revision"],
                    "media": media,
                    "reason": "Неверный слой",
                },
            ).status_code
            == 422
        )
        assert chat.state(sid) == before
        thumb = client.get("/api/v1/media/" + aid + "/thumbnail")
        with Image.open(io.BytesIO(thumb.content)) as image:
            assert (
                image.format == "PNG" and image.size == (24, 32) and not getattr(image, "is_animated", False)
            )

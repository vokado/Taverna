import io
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from taverna.core import AppError
from taverna.story_packages import StoryPackages
from test_chat import append, setup_chat
from test_worlds import apply, attach, setup_world


def test_story_package_all_branches_states_world_memory_and_idempotence(tmp_path):
    world, chat, sid, ids, template, scenario, _ = setup_world(tmp_path / "source")
    attach(world, chat, sid, template, scenario, seed=42)
    first = append(chat, sid, "Начало пути")
    apply(world, chat, sid, operation="time_advance", minutes=60)
    second = append(chat, sid, "Другой эпизод")
    original_bid = chat.branch(sid)["id"]
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"], through_id=first["id"])
    append(chat, sid, "Альтернатива")
    from taverna.profiles import BindingWrite, ProfileWrite

    profile = chat.generation.profiles.write(
        ProfileWrite(expected_revision=0, name="Профиль истории", patch={"parameters": {"temperature": 0.35}})
    )
    chat.generation.profiles.bind(
        BindingWrite(expected_revision=0, scope="story", scope_id=sid, profile_id=profile["id"])
    )
    exported = StoryPackages(chat).export(sid)
    target, _, _ = setup_chat(tmp_path / "target")
    packages = StoryPackages(target)
    preview = packages.preview(exported)
    assert preview["name"] == "Независимая история" and not preview["conflicts"]
    with pytest.raises(AppError, match="Подтвердите"):
        packages.apply(exported, False, preview["sha256"])
    result = packages.apply(exported, True, preview["sha256"])
    assert result["story_id"] == sid
    assert target.messages(sid)["items"][-1]["text"] == "Альтернатива"
    target.switch(sid, original_bid, target.story(sid)["revision"])
    assert target.messages(sid)["items"][-1]["id"] == second["id"]
    assert target.state(sid)["payload"]["world"]["clock"]["minutes"] >= 60
    assert set(target.story(sid)["payload"]["character_snapshots"]) == set(ids)
    assert target.generation.profiles.resolve(story_id=sid)["settings"]["parameters"]["temperature"] == 0.35
    assert target.store.integrity()["ok"]
    # Switching the active branch changes the story, so a repeat cannot silently replace it.
    assert packages.preview(exported)["conflicts"]
    with pytest.raises(AppError, match="не заменяет"):
        packages.apply(exported, True, preview["sha256"])
    pristine, _, _ = setup_chat(tmp_path / "pristine")
    clean = StoryPackages(pristine)
    clean.apply(exported, True, preview["sha256"])
    assert clean.apply(exported, True, preview["sha256"])["already_present"]


def mutate_package(raw, change):
    with ZipFile(io.BytesIO(raw)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    change(files, manifest)
    files["manifest.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


def test_story_package_rejects_paths_checksum_scope_and_partial_publication(tmp_path):
    from taverna.storage import digest

    chat, sid, _ = setup_chat(tmp_path / "source")
    append(chat, sid, "Текст")
    raw = StoryPackages(chat).export(sid)
    target, _, _ = setup_chat(tmp_path / "target")
    packages = StoryPackages(target)

    def wrong_path(files, manifest):
        files["../secret"] = b"x"

    def wrong_checksum(files, manifest):
        files[manifest["documents"][0]["file"]] = b"{}"

    def wrong_scope(files, manifest):
        entry = manifest["documents"][0]
        entry["key"] = "config/session.json"

    def wrong_message(files, manifest):
        for entry in manifest["documents"]:
            value = json.loads(files[entry["file"]])
            if value["kind"] == "message_segment":
                value["payload"]["messages"][0]["text"] = 42
                data = json.dumps(value).encode()
                files[entry["file"]] = data
                entry["sha256"] = digest(data)

    before = target.store.integrity()["documents"]
    for change in (wrong_path, wrong_checksum, wrong_scope, wrong_message):
        broken = mutate_package(raw, change)
        with pytest.raises(AppError):
            packages.preview(broken)
        assert target.store.integrity()["documents"] == before
    assert target.store.get(target.story_key(sid)) is None


def test_package_png_portrait_and_binary_api_preview_import(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.cards import save_portrait
    from taverna.characters import CharacterRequest, CharacterService
    from taverna.core import Config
    from taverna.main import create_app
    from taverna.storage import Mutation, document
    from taverna.worlds import WorldService

    chat, sid, ids = setup_chat(tmp_path / "source")
    # An actual PNG asset is transferred and checked byte for byte.
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (120, 20, 30)).save(buffer, format="PNG")
    png = buffer.getvalue()
    media = document("media_asset", save_portrait(chat.store.root, png))
    chat.store.transaction([Mutation(f"media/{media['id']}.json", media, 0)])
    card = chat.store.get(f"characters/{ids[0]}.json")
    card["payload"]["portrait_id"] = media["id"]
    CharacterService(chat.store, chat.generation).save(
        CharacterRequest(expected_revision=card["revision"], character=card["payload"]), ids[0]
    )
    sid = chat.create("История с портретом", ids)["id"]
    WorldService(chat)
    raw = StoryPackages(chat).export(sid)
    app = create_app(Config(tmp_path / "target"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = app.state.sessions.bootstrap()
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": token}, headers={"Origin": "http://127.0.0.1:8765"}
        ).json()["csrf"]
        headers = {
            "Origin": "http://127.0.0.1:8765",
            "X-Taverna-CSRF": csrf,
            "Content-Type": "application/octet-stream",
        }
        preview = client.post("/api/v1/story-packages/preview", content=raw, headers=headers)
        assert preview.status_code == 200, preview.text
        assert preview.json()["asset_count"] == 1
        response = client.post(
            "/api/v1/story-packages/import",
            content=raw,
            headers={**headers, "X-Confirm-Import": "true", "X-Package-SHA256": preview.json()["sha256"]},
        )
        assert response.status_code == 200, response.text
        stored = app.state.store.get(f"media/{media['id']}.json")
        assert (app.state.store.root / stored["payload"]["relative_path"]).read_bytes() == png
        assert app.state.store.integrity()["ok"]

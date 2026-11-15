import asyncio
import copy
import io
import zipfile
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from taverna.book_export import BookExporter
from taverna.core import AppError
from taverna.manuscript_models import BookAI, Manuscript
from taverna.manuscripts import ManuscriptService
from taverna.media import MediaService
from taverna.worlds import WorldService
from test_chat import setup_chat


def setup(tmp_path):
    chat, sid, characters = setup_chat(tmp_path, greeting="Старый моряк вошёл в порт.")
    story = chat.story(sid)
    service = ManuscriptService(MediaService(WorldService(chat)))
    cid, other = str(uuid4()), str(uuid4())
    book = Manuscript(
        title="Книга «Море»",
        author="Автор",
        chapters=[
            {
                "id": cid,
                "title": "Начало",
                "text": "Кириллица & <script>alert(1)</script>\n\nСледующий абзац.",
                "links": [{"label": "Дальше", "target": "#chapter-" + other}],
            },
            {"id": other, "title": "Финал", "text": "Конец."},
        ],
    ).model_dump(mode="json")
    mid = str(uuid4())
    return service, chat, story, mid, cid, book


def test_book_versions_cas_restore_and_literal_search(tmp_path):
    service, chat, story, mid, cid, book = setup(tmp_path)
    source = copy.deepcopy(chat.story(story["id"]))
    row = service.save(mid, 0, book, "Первая версия")
    assert row["revision"] == 1
    changed = copy.deepcopy(book)
    changed["chapters"][0]["text"] = "Новая проза"
    assert service.save(mid, 1, changed, "Правка")["revision"] == 2
    with pytest.raises(AppError):
        service.save(mid, 1, book, "Устаревшая правка")
    assert [v["payload"]["number"] for v in service.history(mid)["items"]] == [2, 1]
    restored = service.restore(mid, 1, 2)
    assert restored["revision"] == 3 and restored["payload"] == book
    assert len(service.list("КИРИЛЛИЦА")["items"]) == 1
    assert not service.list(".*")["items"]
    assert chat.story(story["id"]) == source
    assert not list(tmp_path.rglob("*.db"))


@pytest.mark.parametrize(
    "target",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:secret@example.org",
        "https://example.org/\nattack",
        "#chapter-" + str(uuid4()),
        "https://example.org/\\evil",
        "https://example.org:not-a-port",
    ],
)
def test_unsafe_links_and_missing_chapter_rejected(target):
    with pytest.raises(ValueError):
        Manuscript(title="x", chapters=[{"title": "x", "links": [{"label": "x", "target": target}]}])


def test_duplicate_chapters_and_xml_controls_rejected():
    cid = str(uuid4())
    with pytest.raises(ValueError):
        Manuscript(title="x", chapters=[{"id": cid, "title": "a"}, {"id": cid, "title": "b"}])
    with pytest.raises(ValueError):
        Manuscript(title="x\x00")
    Manuscript(title=r"Текст \u0000 не является NUL")


def test_all_exports_real_structure_links_and_media(tmp_path):
    service, chat, story, mid, cid, book = setup(tmp_path)
    fixtures = Path(__file__).parent / "fixtures/media"
    image = service.media.import_raw((fixtures / "expression.webp").read_bytes(), "Эмоция", "expression")
    audio = service.media.import_raw((fixtures / "tone.mp3").read_bytes(), "Тон", "music")
    book["chapters"][0]["media"] = [
        {"asset_id": image["id"], "caption": "Портрет"},
        {"asset_id": audio["id"], "caption": "Музыка"},
    ]
    service.save(mid, 0, book, "Импорт")
    exporter = BookExporter(service)
    raw, mime, name = exporter.export(mid, "txt", 1)
    assert "Кириллица" in raw.decode("utf-8") and name == "book.txt"
    raw, mime, name = exporter.export(mid, "markdown", 1)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        md = z.read("book.md").decode("utf-8")
        assert "\\<script\\>" in md and "media/" + image["id"] + ".webp" in md
        assert len([n for n in z.namelist() if n.startswith("media/")]) == 2
    raw, mime, name = exporter.export(mid, "html", 1)
    text = raw.decode("utf-8")
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert "data:image/webp;base64," in text and "data:audio/mpeg;base64," in text
    assert 'id="chapter-' + cid + '"' in text
    raw, mime, name = exporter.export(mid, "epub", 1)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        package = ET.fromstring(z.read("EPUB/package.opf"))
        ns = {"o": "http://www.idpf.org/2007/opf"}
        manifest = {i.attrib["id"]: i.attrib for i in package.find("o:manifest", ns)}
        assert len(package.find("o:spine", ns)) == 2
        assert any(a["media-type"] == "image/png" for a in manifest.values())
        for entry in manifest.values():
            assert "EPUB/" + entry["href"] in z.namelist()
        for name in z.namelist():
            if name.endswith((".xml", ".opf", ".xhtml")):
                ET.fromstring(z.read(name))
        chapter = z.read("EPUB/chapter-" + cid + ".xhtml").decode("utf-8")
        assert "chapter-" + book["chapters"][1]["id"] + ".xhtml#chapter-" in chapter
    with pytest.raises(AppError):
        exporter.export(mid, "epub", 2)
    wav = service.media.import_raw((fixtures / "tone.wav").read_bytes(), "Тон", "music")
    book["chapters"][0]["media"] = [{"asset_id": wav["id"], "caption": "WAV"}]
    service.save(mid, 1, book, "WAV")
    with pytest.raises(AppError, match="EPUB"):
        exporter.export(mid, "epub", 2)


@pytest.mark.asyncio
async def test_ai_proposals_reject_stale_accept_coherence_and_chat_untouched(tmp_path):
    service, chat, story, mid, cid, book = setup(tmp_path)
    service.save(mid, 0, book, "Первый")

    async def generate(payload, cancel, emit):
        assert payload["service_role"] == "editor"
        return {"text": "Связная новая проза.", "context_trace_id": str(uuid4())}

    service.generation.run = generate

    async def emit(*args):
        pass

    request = BookAI(
        operation="shorten", manuscript_id=mid, chapter_id=cid, expected_revision=1, start=0, end=9
    ).model_dump(mode="json")
    result = await service.run(
        {"request": request, "endpoint": "http://127.0.0.1:1234"}, asyncio.Event(), emit
    )
    proposal = result["proposal"]
    assert service.get(mid)["payload"] == book
    assert proposal["payload"]["diff"]
    service.review(proposal["id"], 1, False, False)
    assert service.get(mid)["revision"] == 1
    result = await service.run(
        {"request": request, "endpoint": "http://127.0.0.1:1234"}, asyncio.Event(), emit
    )
    accepted = service.review(result["proposal"]["id"], 1, True, True)
    assert accepted["manuscript"]["revision"] == 2
    request["expected_revision"] = 2
    result = await service.run(
        {"request": request, "endpoint": "http://127.0.0.1:1234"}, asyncio.Event(), emit
    )
    service.save(mid, 2, book, "Параллельная правка")
    with pytest.raises(AppError):
        service.review(result["proposal"]["id"], 1, True, True)
    assert service.get(mid)["revision"] == 3
    request.update(expected_revision=3, operation="coherence")
    result = await service.run(
        {"request": request, "endpoint": "http://127.0.0.1:1234"}, asyncio.Event(), emit
    )
    with pytest.raises(AppError, match="связности"):
        service.review(result["proposal"]["id"], 1, True, True)
    branch = chat.branch(story["id"])
    messages = chat._prefix(story["id"], branch["id"])
    old = copy.deepcopy(messages)
    request = BookAI(
        operation="chat_to_prose",
        story_id=story["id"],
        branch_id=branch["id"],
        branch_revision=branch["revision"],
        message_ids=[m["id"] for m in messages],
    ).model_dump(mode="json")
    result = await service.run(
        {"request": request, "endpoint": "http://127.0.0.1:1234"}, asyncio.Event(), emit
    )
    converted = service.review(result["proposal"]["id"], 1, True, True)["manuscript"]
    assert converted["id"] != mid and converted["payload"]["chapters"][0]["text"] == "Связная новая проза."
    assert chat._prefix(story["id"], branch["id"]) == old and chat.branch(story["id"]) == branch


def test_editor_api_drafts_invalid_raw_cas_and_attachment_exports(tmp_path):
    from test_bootstrap import client, login

    app, c = client(tmp_path, max_body=4 * 1024 * 1024)
    headers = login(app, c)
    mid = str(uuid4())
    payload = Manuscript(title="Кириллица", chapters=[{"title": "Первая", "text": "Текст"}]).model_dump(
        mode="json"
    )
    assert (
        c.put(
            "/api/v1/manuscripts/" + mid,
            json={"expected_revision": 0, "payload": payload, "reason": "Первый"},
        ).status_code
        == 403
    )
    saved = c.put(
        "/api/v1/manuscripts/" + mid,
        json={"expected_revision": 0, "payload": payload, "reason": "Первый"},
        headers=headers,
    )
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    for fmt in ["txt", "markdown", "html", "epub"]:
        response = c.get(f"/api/v1/manuscripts/{mid}/export?format={fmt}&expected_revision=1")
        assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    did = str(uuid4())
    raw = '{"chapters": ['
    draft_payload = copy.deepcopy(payload)
    draft_payload["chapters"][0]["id"] = "not-yet-a-uuid"
    response = c.put(
        "/api/v1/manuscripts/drafts/" + did,
        json={
            "expected_revision": 0,
            "template_id": mid,
            "source_revision": 1,
            "raw": raw,
            "payload": draft_payload,
        },
        headers=headers,
    )
    assert response.status_code == 200 and response.json()["payload"]["raw"] == raw
    assert (
        c.put(
            "/api/v1/manuscripts/" + mid,
            json={"expected_revision": 1, "payload": draft_payload, "reason": "invalid"},
            headers=headers,
        ).status_code
        == 422
    )
    assert app.state.manuscripts.get(mid)["revision"] == 1
    uploaded = c.post(
        "/api/v1/library/import?filename=note.txt&name=Note&confirmed=true",
        content="Кириллица".encode(),
        headers=headers,
    )
    assert uploaded.status_code == 200
    file = c.get("/api/v1/library/" + uploaded.json()["id"] + "/file")
    assert (
        file.status_code == 200
        and "attachment" in file.headers["content-disposition"]
        and file.content == "Кириллица".encode()
    )
    tool = {
        "format": 1,
        "kind": "note",
        "title": "Заметка",
        "text": "Текст",
        "references": [],
        "links": [],
        "archived": False,
        "generator": None,
        "template": None,
    }
    tid = str(uuid4())
    r = c.put("/api/v1/tools/" + tid, json={"expected_revision": 0, "payload": tool}, headers=headers)
    assert r.status_code == 200
    assert len(c.get("/api/v1/tools?query=ТЕКСТ").json()["items"]) == 1
    raw = '{"unclosed":'
    tool["references"] = [
        {
            "story_id": "unfinished-id",
            "branch_id": None,
            "world_id": None,
            "scene_id": None,
            "event_id": None,
            "message_id": None,
        }
    ]
    response = c.put(
        "/api/v1/owner-drafts/tool/" + str(uuid4()),
        json={"expected_revision": 0, "template_id": tid, "source_revision": 1, "raw": raw, "payload": tool},
        headers=headers,
    )
    assert response.status_code == 200 and response.json()["payload"]["raw"] == raw


def test_empty_author_is_omitted_from_epub_metadata(tmp_path):
    service, chat, story, mid, cid, book = setup(tmp_path)
    book["author"] = ""
    service.save(mid, 0, book, "Без автора")
    raw, _, _ = BookExporter(service).export(mid, "epub", 1)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read("EPUB/package.opf"))
        assert root.find(".//{http://purl.org/dc/elements/1.1/}creator") is None
    with pytest.raises(ValueError):
        Manuscript(title="  ")
    with pytest.raises(ValueError):
        Manuscript(title="Book", chapters=[{"title": "\t"}])


@pytest.mark.asyncio
async def test_editor_uses_loaded_only_adapter_and_failure_cancel_keep_original(tmp_path):
    from test_generation import adapter

    service, chat, story, mid, cid, book = setup(tmp_path)
    service.save(mid, 0, book, "Первый")
    calls = []
    service.generation.adapter = adapter(calls)
    request = BookAI(
        operation="rewrite",
        manuscript_id=mid,
        chapter_id=cid,
        expected_revision=1,
        start=0,
        end=9,
        model_id="loaded-instance",
    ).model_dump(mode="json")

    async def emit(*args):
        pass

    result = await service.run(
        {"request": request, "endpoint": service.generation.adapter.endpoint}, asyncio.Event(), emit
    )
    assert result["proposal"]["payload"]["after"] == "Привет"
    assert service.get(mid)["payload"] == book
    assert all(path in ("/api/v1/models", "/v1/chat/completions") for method, path in calls)
    service.generation.adapter = adapter(calls, broken=True)
    with pytest.raises(AppError):
        await service.run(
            {"request": request, "endpoint": service.generation.adapter.endpoint}, asyncio.Event(), emit
        )
    assert service.get(mid)["payload"] == book
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises((AppError, asyncio.CancelledError)):
        await service.run({"request": request, "endpoint": service.generation.adapter.endpoint}, cancel, emit)
    rows, _ = service.store.scan("manuscripts/proposals/")
    assert len(rows) == 1


def test_book_listing_is_paged_and_does_not_read_history(tmp_path, monkeypatch):
    from taverna.storage import ReadView

    service, chat, story, mid, cid, book = setup(tmp_path)
    service.save(mid, 0, book, "Первая")
    service.save(mid, 1, book, "Вторая")
    for number in range(3):
        service.save(str(uuid4()), 0, {**book, "title": "Книга " + str(number)}, "Импорт")
    original = ReadView.get
    reads = []

    def get(view, key):
        reads.append(key)
        return original(view, key)

    monkeypatch.setattr(ReadView, "get", get)
    page = service.list("", offset=1, limit=2)
    assert page["total"] == 4 and len(page["items"]) == 2 and page["offset"] == 1
    assert all("chapters" not in v["payload"] for v in page["items"])
    assert not any("/versions/" in key for key in reads)

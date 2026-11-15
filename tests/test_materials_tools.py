import copy
import io
import zipfile
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.actions import JavaScript
from taverna.core import AppError
from taverna.material_probe import inspect_document
from taverna.materials import MaterialService
from taverna.tool_models import NameRun, TemplatePreview, ToolEntry
from taverna.tools_service import ToolService
from test_manuscripts import setup


def docx(active=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Материал о гавани</w:t></w:r></w:p></w:body></w:document>',
        )
        if active:
            z.writestr(
                "word/_rels/document.xml.rels",
                '<Relationships><Relationship TargetMode="External" Target="file:///etc/passwd" /></Relationships>',
            )
    return out.getvalue()


def pdf(active=False):
    out = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    if active:
        w.add_open_action(JavaScript("app.alert(1)"))
    w.write(out)
    return out.getvalue()


def test_documents_validated_immutable_cas_and_safe_references(tmp_path):
    manuscripts, chat, story, mid, cid, book = setup(tmp_path)
    service = MaterialService(manuscripts.media)
    before = copy.deepcopy(chat.branch(story["id"]))
    values = []
    for filename, raw in [
        ("текст.txt", "Тайна гавани".encode()),
        ("справочник.md", b"# Harbor"),
        ("данные.json", b'{"ok":true}'),
        ("книга.pdf", pdf()),
        ("описание.docx", docx()),
    ]:
        value = service.import_raw(raw, filename, {"name": filename})
        values.append(value)
        assert service.checked_file(value["id"])[1].read_bytes() == raw
    assert len(service.list()["items"]) == 5 and len(service.list("ТАЙНА")["items"]) == 1
    value = values[0]
    payload = {k: value["payload"][k] for k in ("name", "description", "tags", "references", "archived")}
    payload.update(
        archived=True,
        references=[
            {
                "story_id": story["id"],
                "branch_id": before["id"],
                "scene_id": chat.state(story["id"])["payload"]["scene"]["id"],
            }
        ],
    )
    updated = service.update(value["id"], 1, payload)
    assert updated["revision"] == 2 and len(service.list()["items"]) == 4
    with pytest.raises(AppError):
        service.update(value["id"], 1, payload)
    payload["references"][0]["branch_id"] = str(uuid4())
    with pytest.raises(AppError):
        service.update(value["id"], 2, payload)
    assert chat.branch(story["id"]) == before
    assert not list(tmp_path.rglob("*.db"))


@pytest.mark.parametrize(
    "raw,filename",
    [
        (b"hello", "../evil.txt"),
        (b"hello", "C:\\evil.txt"),
        (b"<script>1</script>", "evil.html"),
        (b"{broken", "a.json"),
        (b"\xff", "a.txt"),
        (b"foo\x00bar", "a.txt"),
        (b"%PDF-1.7\ninvalid\n%%EOF", "bad.pdf"),
        (pdf(True), "active.pdf"),
        (docx(True), "active.docx"),
    ],
)
def test_material_negative(raw, filename):
    with pytest.raises(AppError):
        inspect_document(raw, filename)


def test_zip_paths_bombs_and_dtd_rejected():
    for filename, content in [
        ("../evil.txt", "x"),
        ("word/vbaProject.bin", "x"),
        ("word/document.xml", '<!DOCTYPE a [<!ENTITY x "evil">]><a>&x;</a>'),
        ("word/document.xml", "x" * 2 * 1024 * 1024),
    ]:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr(filename, content)
        with pytest.raises(AppError):
            inspect_document(out.getvalue(), "bad.docx")


def test_names_reproducible_template_preview_rights_revoke_and_atomic_apply(tmp_path):
    manuscripts, chat, story, mid, cid, book = setup(tmp_path)
    manuscripts.save(mid, 0, book, "Первая")
    materials = MaterialService(manuscripts.media)
    service = ToolService(materials, manuscripts)
    tid = str(uuid4())
    entry = ToolEntry(kind="name_generator", title="Эльфийские имена", generator={}).model_dump(mode="json")
    service.save(tid, 0, entry)
    first = service.names(tid, NameRun(expected_revision=1, seed="seed", count=10))
    second = service.names(tid, NameRun(expected_revision=1, seed="seed", count=10))
    assert (
        first["payload"]["names"] == second["payload"]["names"]
        and first["payload"]["draws"] == second["payload"]["draws"]
    )
    tid = str(uuid4())
    entry = ToolEntry(
        kind="template",
        title="Карточка локации",
        template={"text": "Место: {{место}}", "variables": ["место"]},
    ).model_dump(mode="json")
    service.save(tid, 0, entry)
    preview = service.preview(tid, TemplatePreview(expected_revision=1, inputs={"место": "Гавань"}))
    assert preview["payload"]["text"] == "Место: Гавань" and not preview["payload"]["permission_granted"]
    assert len(service.list(kind="note")["items"]) == 0
    with pytest.raises(AppError):
        service.execute(preview["id"], 1, True)
    entry["template"]["grants"] = ["notes.write"]
    service.save(tid, 1, entry)
    preview = service.preview(tid, TemplatePreview(expected_revision=2, inputs={"место": "Гавань"}))
    with pytest.raises(AppError):
        service.execute(preview["id"], 1, False)
    result = service.execute(preview["id"], 1, True)
    assert service.get(result["result_id"])["payload"]["text"] == "Место: Гавань"
    with pytest.raises(AppError):
        service.execute(preview["id"], 1, True)
    preview = service.preview(tid, TemplatePreview(expected_revision=2, inputs={"место": "Лес"}))
    entry["template"]["grants"] = []
    service.save(tid, 2, entry)
    with pytest.raises(AppError):
        service.execute(preview["id"], 1, True)
    entry["template"].update(action="chapter", grants=["manuscripts.write"])
    service.save(tid, 3, entry)
    preview = service.preview(
        tid,
        TemplatePreview(
            expected_revision=4,
            inputs={"место": "Порт"},
            manuscript_id=mid,
            manuscript_revision=1,
            chapter_title="Глава о порте",
        ),
    )
    service.execute(preview["id"], 1, True)
    assert (
        manuscripts.get(mid)["revision"] == 2
        and manuscripts.get(mid)["payload"]["chapters"][-1]["text"] == "Место: Порт"
    )
    assert len(manuscripts.history(mid)["items"]) == 2
    with pytest.raises(AppError):
        service.preview(
            tid,
            TemplatePreview(
                expected_revision=4, inputs={"неизвестная": "x"}, manuscript_id=mid, manuscript_revision=2
            ),
        )


def test_entry_substitution_is_plain_data_no_eval(tmp_path):
    manuscripts, *_ = setup(tmp_path)
    service = ToolService(MaterialService(manuscripts.media), manuscripts)
    tid = str(uuid4())
    entry = ToolEntry(
        kind="template",
        title="Без исполнения",
        template={"text": "{{данные}}", "variables": ["данные"], "grants": ["notes.write"]},
    ).model_dump(mode="json")
    service.save(tid, 0, entry)
    preview = service.preview(
        tid,
        TemplatePreview(
            expected_revision=1, inputs={"данные": "{{danger}} $(touch /tmp/no) <script>evil</script>"}
        ),
    )
    service.execute(preview["id"], 1, True)
    assert (
        service.get(preview["payload"]["result_id"])["payload"]["text"]
        == "{{danger}} $(touch /tmp/no) <script>evil</script>"
    )


def test_template_invalid_chapter_does_not_publish_or_consume_preview(tmp_path):
    manuscripts, chat, story, mid, cid, book = setup(tmp_path)
    manuscripts.save(mid, 0, book, "Первый")
    service = ToolService(MaterialService(manuscripts.media), manuscripts)
    tid = str(uuid4())
    entry = ToolEntry(
        kind="template",
        title="Недопустимый текст",
        template={"action": "chapter", "text": "NUL: \x00", "variables": [], "grants": ["manuscripts.write"]},
    ).model_dump(mode="json")
    service.save(tid, 0, entry)
    preview = service.preview(
        tid, TemplatePreview(expected_revision=1, inputs={}, manuscript_id=mid, manuscript_revision=1)
    )
    with pytest.raises(AppError) as error:
        service.execute(preview["id"], 1, True)
    assert error.value.status == 422
    assert manuscripts.get(mid)["revision"] == 1 and manuscripts.get(mid)["payload"] == book
    assert (
        service.store.get("manuscripts/tool-previews/" + preview["id"] + ".json")["payload"]["status"]
        == "pending"
    )

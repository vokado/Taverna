import base64
import io
import json
from zipfile import ZipFile

import pytest
from taverna.cards import (
    export_card,
    export_png,
    import_png,
    make_package,
    new_card,
    normalize_card,
    parse_json,
    read_package,
)
from taverna.characters import CharacterRequest, CharacterService, ExportRequest, ImportRequest
from taverna.core import AppError
from taverna.storage import JsonStore


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_json_png_roundtrip_preserves_unknown_metadata(version):
    payload = new_card("Лесная хранительница")
    payload["fields"].update(
        description="Описание", extensions={"vendor.example": {"secret_field": [1, 2, 3]}}
    )
    card, _ = export_card(payload, version)
    if version == "v1":
        card["unknown_vendor"] = {"preserve": True}
    else:
        card["unknown_root"] = {"preserve": True}
        card["data"]["unknown_field"] = "Не терять"
    imported = normalize_card(card)
    exported, _ = export_card(imported, version)
    assert exported == card
    png, _ = export_png(imported, version)
    assert import_png(png)["fields"] == imported["fields"]
    png2, _ = export_png(imported, version, png)
    assert import_png(png2)["original"] == exported


def test_conversion_reports_v3_and_taverna_loss():
    payload = new_card()
    payload["fields"]["nickname"] = "Сова"
    payload["taverna"]["biography"] = "Длинная биография"
    card, report = export_card(payload, "v2")
    assert "nickname" not in card["data"]
    assert "data.nickname" in report["unsupported_fields"]
    assert "taverna.biography" in report["unsupported_fields"]
    package = make_package(payload, {})
    restored, assets = read_package(package)
    assert restored == payload and not assets


def test_import_preview_does_not_write_then_portrait_atomic_commit(tmp_path):
    service = CharacterService(JsonStore(tmp_path), None)
    payload = new_card("Мира")
    png, _ = export_png(payload, "v3")
    request = ImportRequest(format="png", content_base64=base64.b64encode(png).decode())
    preview = service.import_card(request)
    assert preview["saved"] is False
    assert not service.store.scan("characters/")[0]
    assert not list((tmp_path / "media").rglob("*.png"))
    request.confirmed = True
    created = service.import_card(request)["character"]
    assert created["payload"]["fields"]["name"] == "Мира"
    assert created["payload"]["portrait_id"]
    package, report = service.export(created["id"], ExportRequest(format="package"))
    restored = service.import_card(
        ImportRequest(format="package", content_base64=base64.b64encode(package).decode(), confirmed=True)
    )["character"]
    assert restored["id"] != created["id"]
    assert restored["payload"]["portrait_id"] != created["payload"]["portrait_id"]
    assert restored["payload"]["fields"] == created["payload"]["fields"]
    assert service.store.integrity()["ok"]


def test_rename_preserves_identity_metadata_and_expert_extensions(tmp_path):
    service = CharacterService(JsonStore(tmp_path), None)
    payload = new_card("Старое имя")
    payload["fields"]["extensions"] = {"vendor": {"deep": ["поле"]}}
    old = service.save(CharacterRequest(character=payload, expected_revision=0))
    payload["fields"]["name"] = "Новое имя"
    updated = service.save(CharacterRequest(character=payload, expected_revision=1), old["id"])
    assert updated["id"] == old["id"] and updated["revision"] == 2
    assert updated["payload"]["fields"]["extensions"] == {"vendor": {"deep": ["поле"]}}
    with pytest.raises(AppError):
        service.save(CharacterRequest(character=payload, expected_revision=1), old["id"])


def test_malformed_card_png_and_package_paths_are_rejected():
    with pytest.raises(AppError):
        normalize_card({"name": "нет обязательных полей"})
    with pytest.raises(AppError):
        parse_json(b'{"name":"a","name":"b"}')
    png, _ = export_png(new_card(), "v3")
    bad = bytearray(png)
    bad[50] ^= 1
    with pytest.raises(AppError):
        import_png(bytes(bad))
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("../evil.json", "{}")
    with pytest.raises(AppError):
        read_package(stream.getvalue())


def test_script_content_is_data_and_never_executed(tmp_path):
    service = CharacterService(JsonStore(tmp_path), None)
    payload = new_card("<script>alert(1)</script>")
    payload["fields"]["description"] = "$(touch /tmp/should-not-exist)"
    saved = service.save(CharacterRequest(character=payload, expected_revision=0))
    assert saved["payload"]["fields"]["name"] == payload["fields"]["name"]
    raw, _ = service.export(saved["id"], ExportRequest())
    assert json.loads(raw)["data"]["description"] == payload["fields"]["description"]

"""Independent checks for full portable backups.

These tests do not claim scheduled backups, restore UI or Windows 11 acceptance.
"""
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from taverna.core import AppError
from taverna.full_backup import (
    create_full_backup,
    restore_full_backup_to_new_directory,
    verify_full_backup,
)
from taverna.storage import JsonStore, Mutation, digest, document, encoded, safe_path


def attach(store, relative, body):
    location = safe_path(store.root, relative)
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_bytes(body)
    return digest(body)


def sample_store(tmp_path):
    store = JsonStore(tmp_path)
    image = b"not-decoded-media-by-design"
    image_sha = digest(image)
    image_path = f"media/assets/{image_sha}.png"
    attach(store, image_path, image)
    doc = b"Notebook contents"
    doc_sha = digest(doc)
    doc_path = f"library/documents/{doc_sha}.txt"
    attach(store, doc_path, doc)
    pid = str(uuid4())
    plugin_dir = f"plugins/{pid}"
    plugin_data = b'{"name":"test"}'
    plugin_sha = attach(store, plugin_dir + "/manifest.json", plugin_data)
    records = [
        ("characters/sample.json", document("character", {"name": "Мира"})),
        (
            "media/image.json",
            document("media_asset", {"relative_path": image_path, "checksum": image_sha}),
        ),
        (
            "media/materials/note.json",
            document("material", {"relative_path": doc_path, "checksum": doc_sha}),
        ),
        (
            "automations/plugins/sample.json",
            document(
                "plugin_registration",
                {"directory": plugin_dir, "files": {"manifest.json": plugin_sha}},
            ),
        ),
    ]
    store.transaction([Mutation(key, value, 0) for key, value in records])
    return store, [image_path, doc_path, plugin_dir + "/manifest.json"]


def test_full_backup_contains_only_published_json_and_referenced_assets(tmp_path):
    store, assets = sample_store(tmp_path)
    # Transient runtime metadata and orphan binaries must not enter portable backup.
    (store.root / "data/config/launcher.key").write_text("SECRET")
    attach(store, "media/assets/orphan.png", b"unreferenced")
    archive = create_full_backup(store, reason="manual")
    content = verify_full_backup(archive)
    with ZipFile(archive) as zipped:
        names = set(zipped.namelist())
    assert "data/config/store-manifest.json" in names
    assert all(x in names for x in assets)
    assert "data/config/launcher.key" not in names
    assert "media/assets/orphan.png" not in names
    assert content["generation"] == 1
    target = store.root.parent / "restored"
    restore_full_backup_to_new_directory(archive, target)
    recovered = JsonStore(target)
    assert recovered.integrity()["ok"]
    assert recovered.get("characters/sample.json")["payload"]["name"] == "Мира"
    assert all((target / x).is_file() for x in assets)
    assert not (target / "data/config/launcher.key").exists()


def test_missing_or_modified_asset_aborts_without_publishing(tmp_path):
    store, assets = sample_store(tmp_path)
    image = store.root / assets[0]
    image.write_bytes(b"changed")
    with pytest.raises(AppError, match="Контрольная сумма"):
        create_full_backup(store)
    assert not list((tmp_path / "backups").glob("*.zip"))
    assert not list((tmp_path / "backups").glob("*.tmp"))
    image.unlink()
    with pytest.raises(AppError, match="отсутствует"):
        create_full_backup(store)
    assert not list((tmp_path / "backups").glob("*.zip"))


def test_tamper_archive_and_duplicate_entry_fail_verification(tmp_path):
    store, assets = sample_store(tmp_path)
    archive = create_full_backup(store)
    with ZipFile(archive, "a", ZIP_DEFLATED) as zipped:
        zipped.writestr(assets[0], b"bad")
    with pytest.raises(AppError) as error:
        verify_full_backup(archive)
    assert error.value.code == "invalid_full_backup"


def test_existing_destination_is_never_overwritten(tmp_path):
    store, _ = sample_store(tmp_path)
    archive = create_full_backup(store)
    destination = tmp_path.parent / "existing"
    destination.mkdir()
    (destination / "important.txt").write_text("KEEP")
    with pytest.raises(AppError) as error:
        restore_full_backup_to_new_directory(archive, destination)
    assert error.value.code == "restore_destination_exists"
    assert (destination / "important.txt").read_text() == "KEEP"


def test_bad_relative_path_is_rejected_before_archive_publication(tmp_path):
    store = JsonStore(tmp_path)
    store.transaction(
        [
            Mutation(
                "media/unsafe.json",
                document(
                    "media_asset",
                    {"relative_path": "media/../bad.txt", "checksum": digest(b"bad")},
                ),
                0,
            )
        ]
    )
    with pytest.raises(AppError) as error:
        create_full_backup(store)
    assert error.value.code == "backup_source_invalid"
    assert not list((tmp_path / "backups").glob("*.zip"))


def test_missing_referenced_asset_is_rejected_even_with_rewritten_manifest(tmp_path):
    store, assets = sample_store(tmp_path)
    original = create_full_backup(store)
    with ZipFile(original) as source:
        contents = {info.filename: source.read(info) for info in source.infolist()}
    del contents[assets[0]]
    manifest = json.loads(contents["backup-manifest.json"])
    del manifest["files"][assets[0]]
    contents["backup-manifest.json"] = encoded(manifest)
    altered = tmp_path / "altered.zip"
    with ZipFile(altered, "w", ZIP_DEFLATED) as zipped:
        for name, raw in contents.items():
            zipped.writestr(name, raw)
    with pytest.raises(AppError) as error:
        verify_full_backup(altered)
    assert error.value.code == "invalid_full_backup"


def test_unrelated_executable_is_rejected_even_when_it_has_valid_checksum(tmp_path):
    store, _ = sample_store(tmp_path)
    original = create_full_backup(store)
    with ZipFile(original) as source:
        contents = {info.filename: source.read(info) for info in source.infolist()}
    manifest = json.loads(contents["backup-manifest.json"])
    path = "plugins/unrelated.py"
    contents[path] = b"print('untrusted payload')"
    manifest["files"][path] = digest(contents[path])
    contents["backup-manifest.json"] = encoded(manifest)
    altered = tmp_path / "extra.zip"
    with ZipFile(altered, "w", ZIP_DEFLATED) as zipped:
        for name, raw in contents.items():
            zipped.writestr(name, raw)
    with pytest.raises(AppError) as error:
        verify_full_backup(altered)
    assert error.value.code == "invalid_full_backup"


def test_restore_rechecks_bytes_when_zip_is_replaced_after_preflight(tmp_path, monkeypatch):
    store, assets = sample_store(tmp_path)
    archive = create_full_backup(store)
    original_manifest = verify_full_backup(archive)

    def replaced_after_verification(path):
        with ZipFile(path) as source:
            contents = {info.filename: source.read(info) for info in source.infolist()}
        contents[assets[0]] = b"unexpected-replacement"
        with ZipFile(path, "w", ZIP_DEFLATED) as output:
            for name, value in contents.items():
                output.writestr(name, value)
        return original_manifest

    monkeypatch.setattr(
        "taverna.full_backup.verify_full_backup", replaced_after_verification
    )
    destination = tmp_path.parent / "changed-restore"
    with pytest.raises(AppError) as exc:
        restore_full_backup_to_new_directory(archive, destination)
    assert exc.value.code == "invalid_full_backup"
    assert not destination.exists()
    assert not list(tmp_path.parent.glob(".changed-restore.*.restore"))


def test_large_asset_is_streamed_to_zip_without_read_bytes(tmp_path, monkeypatch):
    store = JsonStore(tmp_path)
    blob = b"scene-image" * 500_000
    checksum = digest(blob)
    relative = f"media/assets/{checksum}.png"
    asset = tmp_path / relative
    attach(store, relative, blob)
    store.transaction(
        [
            Mutation(
                "media/large.json",
                document("media_asset", {"relative_path": relative, "checksum": checksum}),
                0,
            )
        ]
    )

    original_read = Path.read_bytes

    def forbid_whole_asset_read(path):
        if path == asset:
            raise AssertionError("Backup must stream assets, not read_bytes() the complete file")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", forbid_whole_asset_read)
    archive = create_full_backup(store)
    verified = verify_full_backup(archive)
    assert verified["files"][relative] == checksum
    with ZipFile(archive) as zipped:
        assert zipped.getinfo(relative).file_size == len(blob)

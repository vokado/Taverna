"""Safe portable manual upgrade: source stays untouched and rollback stays possible."""

import hashlib
import io
import stat
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from taverna.core import AppError
from taverna.manual_update import prepare_update
from taverna.storage import JsonStore, Mutation, document


_REQUIRED = (
    "Taverna.exe",
    "Taverna-Diagnostics.exe",
    "Taverna-PluginHost.exe",
    "app/web/dist/index.html",
)


def make_source(root):
    root.mkdir()
    (root / "Taverna.exe").write_bytes(b"old program")
    store = JsonStore(root)
    store.transaction([
        Mutation("characters/one.json", document("test", {"name": "Мира"}), 0)
    ])
    (root / "media" / "picture with spaces.txt").write_text("asset", encoding="utf-8")
    return store


def archive_bytes(*, extra=None, missing=None, link=None):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as zipfile:
        for name in _REQUIRED:
            if name != missing:
                zipfile.writestr(name, b"new program or web page")
        zipfile.writestr("LICENSES/Some License (MIT).txt", b"license text")
        if extra:
            zipfile.writestr(extra, b"untrusted")
        if link:
            info = ZipInfo(link)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipfile.writestr(info, "outside")
    return buffer.getvalue()


def write_zip(root, *, extra=None, missing=None, link=None):
    path = root / "new.zip"
    path.write_bytes(archive_bytes(extra=extra, missing=missing, link=link))
    return path


def test_update_creates_new_complete_copy_and_preserves_old_install(tmp_path):
    old = tmp_path / "old"
    store = make_source(old)
    zip_path = write_zip(tmp_path)
    good_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    new = tmp_path / "new"
    result = prepare_update(old, zip_path, new, expected_sha256=good_sha)
    assert result["old_install_preserved"] and result["destination"] == str(new)
    assert (old / "Taverna.exe").read_bytes() == b"old program"
    assert (new / "Taverna.exe").read_bytes() == b"new program or web page"
    assert (new / "Taverna-Diagnostics.exe").is_file()
    assert (new / "LICENSES" / "Some License (MIT).txt").read_bytes() == b"license text"
    assert (new / "media" / "picture with spaces.txt").read_text() == "asset"
    assert JsonStore(new).integrity()["ok"]
    assert store.integrity()["ok"]
    assert JsonStore(new).get("characters/one.json")["payload"]["name"] == "Мира"
    assert (old / "backups" / Path(result["backup"]).name).is_file()
    assert (new / "upgrade-record.json").is_file()
    assert not (new / "data/config/launcher.key").exists()
    assert not (new / "data/config/instance.json").exists()


@pytest.mark.parametrize(
    "bad",
    ["../traversal", "/absolute", "data/config/store-manifest.json", "C:drive",
     "a\\b", "CON.txt", "some/../other", "file:name"],
)
def test_unsafe_zip_never_extracted_and_old_code_untouched(tmp_path, bad):
    old = tmp_path / "old"
    make_source(old)
    archive = write_zip(tmp_path, extra=bad)
    with pytest.raises(AppError):
        prepare_update(old, archive, tmp_path / "new")
    assert (old / "Taverna.exe").read_bytes() == b"old program"
    assert not (tmp_path / "new").exists()
    assert not (tmp_path / "traversal").exists()


@pytest.mark.parametrize("change", ["missing", "link", "checksum"])
def test_missing_exe_zip_link_or_bad_digest_rejected(tmp_path, change):
    old = tmp_path / "old"
    make_source(old)
    path = write_zip(
        tmp_path,
        missing="Taverna.exe" if change == "missing" else None,
        link="shortcut" if change == "link" else None,
    )
    with pytest.raises(AppError):
        prepare_update(
            old, path, tmp_path / "new",
            expected_sha256="a" * 64 if change == "checksum" else None,
        )
    assert not (tmp_path / "new").exists()
    assert (old / "Taverna.exe").exists()


def test_failure_after_preupdate_backup_leaves_source_and_destination_unchanged(
    tmp_path, monkeypatch
):
    import taverna.manual_update as module

    old = tmp_path / "old"
    make_source(old)
    path = write_zip(tmp_path)

    def fail_copy(source, destination):
        raise OSError("disk full during copy")

    monkeypatch.setattr(module, "_copy_user_tree", fail_copy)
    with pytest.raises(AppError):
        prepare_update(old, path, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert (old / "Taverna.exe").read_bytes() == b"old program"
    assert JsonStore(old).integrity()["ok"]
    assert list((old / "backups").glob("taverna-pre-update-*.zip"))

import io
import json
import stat
import zipfile
from uuid import uuid4

import pytest
import wasmtime
from taverna.core import AppError
from taverna.plugin_packages import PluginPackages


def package(manifest=None, entry=None, extra=None):
    value = {
        "format": 1,
        "id": str(uuid4()),
        "name": "Проверяемое расширение",
        "version": "1.0.0",
        "api_version": 1,
        "runtime": "declarative",
        "entry": "main.json",
        "requests": [{"capability": "notes.write", "reason": "Создание заметки", "scopes": ["new"]}],
    }
    value.update(manifest or {})
    files = {
        "manifest.json": json.dumps(value, ensure_ascii=False).encode(),
        value["entry"]: entry or b'{"format":1,"actions":[],"message":"preview"}',
    }
    files.update(extra or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return out.getvalue()


def test_manifest_inspection_is_readonly_no_code_runs(tmp_path):
    marker = tmp_path / "executed"
    code = (
        "from pathlib import Path\nPath("
        + repr(str(marker))
        + ').write_text("NO")\ndef run(data): return {"actions": []}'
    ).encode()
    preview, files = PluginPackages(tmp_path).inspect(
        package({"runtime": "trusted_python", "entry": "main.py"}, code)
    )
    assert not marker.exists() and not list(tmp_path.iterdir())
    assert (
        preview["application_compatible"]
        and not preview["abi_verified"]
        and "полный доступ" in preview["warning_ru"]
    )
    assert preview["manifest"]["requests"][0]["scopes"] == ["new"]
    assert len(preview["sha256"]) == 64 and files["main.py"] == code


@pytest.mark.parametrize(
    "change",
    [
        {"api_version": 2},
        {"version": "not a version"},
        {"entry": "../main.json"},
        {"dependencies": [{"plugin_id": "not uuid", "version": ">=1"}]},
        {"requests": [{"capability": "give-me-everything", "reason": "x"}]},
        {"fields": [{"key": "x", "label": "A"}, {"key": "x", "label": "B"}]},
        {"unknown": "must-not-drop"},
    ],
)
def test_manifest_invalid_or_unknown_fields_rejected(tmp_path, change):
    with pytest.raises(AppError):
        PluginPackages(tmp_path).inspect(package(change))


@pytest.mark.parametrize(
    "name", ["../evil", "/absolute", "C:evil", "folder\\evil", "CON.txt", "folder/.. /bad", "manifest.JSON"]
)
def test_windows_paths_and_case_collisions_rejected(tmp_path, name):
    with pytest.raises(AppError):
        PluginPackages(tmp_path).inspect(package(extra={name: b"x"}))


def test_archive_links_bomb_compatibility_and_no_wasi(tmp_path):
    parser = PluginPackages(tmp_path)
    with pytest.raises(AppError):
        parser.inspect(package(extra={"huge.txt": b"x" * 1024 * 1024}))
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(AppError):
        parser.inspect(raw.getvalue())
    preview, _ = parser.inspect(package({"application_versions": ">=9"}))
    assert not preview["application_compatible"]
    code = bytes(wasmtime.wat2wasm('(module (import "wasi_snapshot_preview1" "fd_write" (func)))'))
    with pytest.raises(AppError) as error:
        parser.inspect(package({"runtime": "wasm", "entry": "main.wasm"}, code))
    assert error.value.code == "plugin_import_denied"


def test_file_directory_collisions_rejected_without_touching_existing_plugins(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "reserved.lock").write_bytes(b"not a directory")
    parser = PluginPackages(tmp_path)
    with pytest.raises(AppError):
        parser.inspect(package(extra={"a": b"x", "a/b.txt": b"y"}))
    # Entry names are checked as portable package paths, not against unrelated
    # files in the live plugin root before the immutable UUID/hash is selected.
    preview, files = parser.inspect(
        package(extra={"reserved.lock/child.txt": b"valid inside its own package"})
    )
    assert "reserved.lock/child.txt" in files and preview["manifest"]["entry"] == "main.json"
    assert (root / "reserved.lock").read_bytes() == b"not a directory"

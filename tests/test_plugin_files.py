import base64
import os
from pathlib import Path

import pytest
from taverna.core import AppError
from taverna.plugin_files import MAX_FILE, PluginFiles, handle_path
from taverna.plugin_registry import PluginRegistry
from taverna.storage import JsonStore, digest
from test_plugin_packages import package
from test_plugin_registry import configure, install


def setup(tmp_path):
    scope = tmp_path / "Разрешённые документы с пробелами"
    scope.mkdir()
    registry = PluginRegistry(JsonStore(tmp_path / "portable"))
    row = install(
        registry,
        package(
            {
                "requests": [
                    {
                        "capability": "filesystem.read",
                        "reason": "Прочитать выбранный файл",
                        "scopes": [str(scope)],
                    }
                ]
            }
        ),
    )
    row = configure(registry, row, grants=[{"capability": "filesystem.read", "scopes": [str(scope)]}])
    identity = {
        "plugin_id": row["id"],
        "plugin_revision": row["revision"],
        "permission_epoch": row["payload"]["permission_epoch"],
        "package_sha256": row["payload"]["package_sha256"],
        "catalog_revision": registry.list()["catalog_revision"],
    }
    return PluginFiles(registry), identity, scope


def test_real_file_unicode_spaces_boundaries_hash_and_actual_os_handle(tmp_path):
    files, identity, scope = setup(tmp_path)
    folder = scope / "Подкаталог с пробелами"
    folder.mkdir()
    path = folder / "Заметка 01.txt"
    data = "Таверна: владелец выбрал файл".encode()
    path.write_bytes(data)
    with path.open("rb") as stream:
        assert handle_path(stream).resolve() == path.resolve()
    result = files.read(identity, str(scope), "Подкаталог с пробелами/Заметка 01.txt")
    assert base64.b64decode(result["content_base64"]) == data
    assert result["sha256"] == digest(data) and result["bytes"] == len(data)
    path.write_bytes(b"x" * MAX_FILE)
    assert files.read(identity, str(scope), "Подкаталог с пробелами/Заметка 01.txt")["bytes"] == MAX_FILE
    path.write_bytes(b"x" * (MAX_FILE + 1))
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "Подкаталог с пробелами/Заметка 01.txt")
    assert error.value.code == "plugin_file_limit"


@pytest.mark.parametrize(
    "name",
    [
        "../private",
        "/absolute",
        "\\\\server\\share",
        "C:\\secret",
        "file.txt:stream",
        "sub/../file",
        "sub//file",
        "NUL.txt",
        "COM1",
        "COM¹.txt",
        "LPT²",
        "CONIN$",
        "CONOUT$",
        "file.",
        "file ",
        "name\x00.txt",
        "folder/",
        "../portable/data/config/store-manifest.json",
    ],
)
def test_traversal_ads_devices_and_ambiguous_windows_names_rejected(tmp_path, name):
    files, identity, scope = setup(tmp_path)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), name)
    assert error.value.code == "plugin_file_path"


def test_missing_directory_fifo_and_hardlinks_never_read(tmp_path):
    files, identity, scope = setup(tmp_path)
    with pytest.raises(AppError):
        files.read(identity, str(scope), "missing.txt")
    (scope / "directory").mkdir()
    with pytest.raises(AppError):
        files.read(identity, str(scope), "directory")
    source = scope / "source.txt"
    source.write_text("private")
    try:
        os.link(source, scope / "alias.txt")
    except OSError as exc:
        pytest.skip("Hardlink unavailable: " + type(exc).__name__)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "alias.txt")
    assert error.value.code == "plugin_file_type"
    if os.name != "nt":
        os.mkfifo(scope / "fifo")
        with pytest.raises(AppError) as error:
            files.read(identity, str(scope), "fifo")
        assert error.value.code == "plugin_file_type"


def test_scope_and_revoked_identity_checked_before_os_open(tmp_path, monkeypatch):
    files, identity, scope = setup(tmp_path)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "data").write_text("not granted")

    def deny_open(*args, **kwargs):
        raise AssertionError("No file may open for a denied scope")

    with monkeypatch.context() as context:
        context.setattr(os, "open", deny_open)
        with pytest.raises(AppError) as error:
            files.read(identity, str(secret), "data")
        assert error.value.code == "plugin_scope_denied"
    row = files.registry.get(identity["plugin_id"])
    configure(files.registry, row, enabled=False)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "data")
    assert error.value.code in ("plugin_catalog_conflict", "plugin_permission_revoked")


def test_real_symlink_to_application_rejected_without_returning_bytes(tmp_path):
    files, identity, scope = setup(tmp_path)
    secret = files.registry.store.root / "private.txt"
    secret.write_text("application secret")
    try:
        (scope / "link.txt").symlink_to(secret)
    except OSError as exc:
        pytest.skip("Symlink permission unavailable: " + type(exc).__name__)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "link.txt")
    assert error.value.code == "plugin_file_path"


def test_revocation_during_open_prevents_publication(tmp_path, monkeypatch):
    import taverna.plugin_files as module

    files, identity, scope = setup(tmp_path)
    (scope / "file.txt").write_text("selected bytes")

    def revoke(stream):
        actual = handle_path(stream)
        row = files.registry.get(identity["plugin_id"])
        configure(files.registry, row, enabled=False)
        return actual

    monkeypatch.setattr(module, "handle_path", revoke)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "file.txt")
    assert error.value.code == "plugin_catalog_conflict"


def test_file_growth_while_open_is_bounded(tmp_path, monkeypatch):
    import taverna.plugin_files as module

    files, identity, scope = setup(tmp_path)
    file = scope / "file.txt"
    file.write_text("small")

    def grow(stream):
        actual = handle_path(stream)
        file.write_bytes(b"x" * (MAX_FILE + 1))
        return actual

    monkeypatch.setattr(module, "handle_path", grow)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "file.txt")
    assert error.value.code == "plugin_file_limit"


def test_disappearing_authorized_root_is_reported_as_unavailable(tmp_path, monkeypatch):
    files, identity, scope = setup(tmp_path)
    original = files.registry.validate_grants

    def remove_after_validation(data):
        original(data)
        scope.rmdir()

    monkeypatch.setattr(files.registry, "validate_grants", remove_after_validation)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "data")
    assert error.value.code == "plugin_file_unavailable"


def test_root_redirect_after_validation_does_not_authorize_application_target(tmp_path, monkeypatch):
    files, identity, scope = setup(tmp_path)
    (files.registry.store.root / "secret.txt").write_text("Never return application data")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(files.registry.store.root, target_is_directory=True)
        probe.unlink()
    except OSError as exc:
        pytest.skip("Symlink permission unavailable: " + type(exc).__name__)
    original = files.registry.validate_grants

    def redirect_after_validation(data):
        original(data)
        scope.rmdir()
        scope.symlink_to(files.registry.store.root, target_is_directory=True)

    monkeypatch.setattr(files.registry, "validate_grants", redirect_after_validation)
    with pytest.raises(AppError) as error:
        files.read(identity, str(scope), "secret.txt")
    assert error.value.code == "plugin_file_changed"


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 alias requires native NTFS API")
def test_windows_short_directory_alias_matches_long_open_handle(tmp_path):
    import ctypes
    from ctypes import wintypes

    files, identity, scope = setup(tmp_path)
    path = scope / "Материал.txt"
    path.write_bytes(b"approved snapshot")
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    count = function(str(scope), buffer, len(buffer))
    if not count or count >= len(buffer):
        pytest.skip("8.3 alias creation is disabled on this volume")
    alias = Path(buffer.value)
    if alias == scope.resolve():
        pytest.skip("Volume exposes no distinct 8.3 alias for fixture directory")
    row = files.registry.get(identity["plugin_id"])
    # The manifest requested the long path; a new package explicitly requests
    # its alias rather than silently widening an already granted request.
    raw = package(
        {
            "id": row["id"],
            "version": "1.0.1",
            "requests": [{"capability": "filesystem.read", "reason": "8.3 path", "scopes": [str(alias)]}],
        }
    )
    row = install(files.registry, raw)
    row = configure(files.registry, row, grants=[{"capability": "filesystem.read", "scopes": [str(alias)]}])
    identity.update(
        plugin_revision=row["revision"],
        permission_epoch=row["payload"]["permission_epoch"],
        package_sha256=row["payload"]["package_sha256"],
        catalog_revision=files.registry.list()["catalog_revision"],
    )
    assert files.read(identity, str(alias), path.name)["sha256"] == digest(b"approved snapshot")

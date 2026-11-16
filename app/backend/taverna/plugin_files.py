"""Read-only external-file broker primitive; no implicit scope or guest OS handle."""

from __future__ import annotations

import base64
import os
import re
import stat
import unicodedata
from pathlib import Path

from .core import AppError
from .storage import digest, is_redirect

MAX_FILE = 128 * 1024


def relative_parts(name):
    if (
        not name
        or len(name) > 1024
        or name != unicodedata.normalize("NFC", name)
        or name.startswith(("/", "\\"))
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or any(c in name for c in ':*?"<>|')
    ):
        raise AppError("plugin_file_path", "Укажите однозначное относительное имя локального файла.", 422)
    parts = re.split(r"[/\\]", name)
    devices = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in (*range(10), "¹", "²", "³")),
        *(f"LPT{i}" for i in (*range(10), "¹", "²", "³")),
        "CONIN$",
        "CONOUT$",
    }
    if len(parts) > 16 or any(
        p in ("", ".", "..") or len(p) > 240 or p.endswith((" ", ".")) or p.split(".")[0].upper() in devices
        for p in parts
    ):
        raise AppError("plugin_file_path", "Имя содержит переходы или недопустимые компоненты Windows.", 422)
    return parts


def handle_path(stream):
    if os.name != "nt":
        # Linux execution environment. The supported product handle check below is
        # tested separately on Windows; no inference about NTFS from /proc.
        return Path(f"/proc/self/fd/{stream.fileno()}").resolve(strict=True)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(stream.fileno())
    buffer = ctypes.create_unicode_buffer(32768)
    count = function(handle, buffer, len(buffer), 0)
    if not count or count >= len(buffer):
        raise OSError("Cannot validate opened handle path")
    name = buffer.value
    if name.startswith("\\\\?\\UNC\\"):
        raise OSError("Network handle forbidden")
    return Path(name[4:] if name.startswith("\\\\?\\") else name)


class PluginFiles:
    def __init__(self, registry):
        self.registry = registry

    def read(self, identity, scope, name):
        # Authorization and canonical scope checks precede opening any file.
        row = self.registry.require_current(
            identity["plugin_id"],
            identity["plugin_revision"],
            identity["permission_epoch"],
            identity["package_sha256"],
            catalog_revision=identity["catalog_revision"],
        )
        grants = row["payload"]["grants"]
        if not any(g["capability"] == "filesystem.read" and scope in g["scopes"] for g in grants):
            raise AppError("plugin_scope_denied", "Нет разрешения чтения выбранного каталога.", 403)
        parts = relative_parts(name)
        try:
            # Windows handles report long canonical names even when a grant uses
            # an 8.3 alias (e.g. RUNNER~1). Capture that spelling BEFORE validating
            # the live path and bind it to the same directory identity. Resolving
            # after validation could authorize a substituted symlink target.
            lexical_root = Path(scope).absolute()
            initial_root = lexical_root.stat(follow_symlinks=False)
            root = lexical_root.resolve(strict=True)
            canonical_root = root.stat(follow_symlinks=False)
            if not stat.S_ISDIR(initial_root.st_mode) or (initial_root.st_dev, initial_root.st_ino) != (
                canonical_root.st_dev,
                canonical_root.st_ino,
            ):
                raise AppError("plugin_file_changed", "Разрешённый каталог изменился при проверке.", 409)
            self.registry.validate_grants(row["payload"])
            application = self.registry.store.root.resolve()
            if root.is_relative_to(application) or application.is_relative_to(root):
                raise AppError(
                    "plugin_scope_forbidden",
                    "Каталог программы и его предки не доступны файловому broker.",
                    403,
                )
            current_root = lexical_root.stat(follow_symlinks=False)
            if (current_root.st_dev, current_root.st_ino) != (initial_root.st_dev, initial_root.st_ino):
                raise AppError("plugin_file_changed", "Разрешённый каталог подменён после проверки.", 409)
            path = lexical_root
            for part in parts:
                path /= part
                if is_redirect(path):
                    raise AppError("plugin_file_path", "Ссылки и перенаправления запрещены.", 403)
            initial = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
                raise AppError("plugin_file_type", "Разрешены обычные файлы без жёстких ссылок.", 422)
            if initial.st_size > MAX_FILE:
                raise AppError("plugin_file_limit", "Файл больше128КиБ.", 413)
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                actual = handle_path(stream)
                before = os.fstat(stream.fileno())
                if (
                    not actual.is_relative_to(root)
                    or not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino)
                ):
                    raise AppError(
                        "plugin_file_changed", "Открытый файл не совпадает с проверенным объектом.", 409
                    )
                data = stream.read(MAX_FILE + 1)
                after = os.fstat(stream.fileno())
            if len(data) > MAX_FILE:
                raise AppError("plugin_file_limit", "Файл вырос больше128КиБ во время чтения.", 413)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise AppError(
                    "plugin_file_changed", "Файл изменился во время чтения; данные не переданы.", 409
                )
        except AppError:
            raise
        except OSError as exc:
            raise AppError(
                "plugin_file_unavailable", "Файл недоступен или не прошёл проверку пути.", 409
            ) from exc
        # Never publish data after a concurrent grant change. External file bytes
        # are not a storage snapshot; callers must still guard plan publication.
        self.registry.require_current(
            identity["plugin_id"],
            identity["plugin_revision"],
            identity["permission_epoch"],
            identity["package_sha256"],
            catalog_revision=identity["catalog_revision"],
        )
        return {
            "path": "/".join(parts),
            "bytes": len(data),
            "sha256": digest(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

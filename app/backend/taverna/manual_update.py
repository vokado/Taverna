"""Non-destructive manual portable upgrade into a NEW directory.

The currently installed Taverna is left intact. The existing user data is
backed up before copying; no in-place overwrite or update check is performed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import portalocker

from .core import AppError
from .full_backup import create_full_backup
from .storage import JsonStore, is_redirect

_LIMIT_FILES = 150_000
_LIMIT_BYTES = 8 * 1024 * 1024 * 1024
_USER_DIRS = ("data", "media", "library", "plugins", "backups", "logs")
_RUNTIME_NAMES = {
    "data/server.lock",
    "data/config/manager.lock",
    "data/config/instance.json",
    "data/config/launcher.key",
}
_REQUIRED_CODE = {
    "Taverna.exe",
    "Taverna-Diagnostics.exe",
    "Taverna-PluginHost.exe",
    "Taverna-Updater.exe",
    "app/web/dist/index.html",
}


def _update_error(code: str, text: str, status: int = 422) -> AppError:
    return AppError(code, text, status)


_RESERVED_WIN = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}


def _archive_parts(name: str) -> list[str]:
    """Windows-compatible ZIP filename policy, allowing spaces in licenses."""
    if not name or chr(92) in name or name.startswith("/"):
        raise _update_error("update_archive_unsafe", "Недопустимый путь в архиве.")
    parts = name.split("/")
    for part in parts:
        if (
            part in ("", ".", "..")
            or part.endswith((" ", "."))
            or any(ord(c) < 32 or ord(c) == 92 or c in '<>:"|?*' for c in part)
            or part.split(".")[0].upper() in _RESERVED_WIN
        ):
            raise _update_error("update_archive_unsafe", "Недопустимый путь в архиве.")
    return parts


def _safe_entries(archive: ZipFile):
    """Validate the whole ZIP before writing any files to the destination."""
    entries, seen, total = [], set(), 0
    for info in archive.infolist():
        if len(entries) >= _LIMIT_FILES:
            raise _update_error("update_archive_too_large", "В архиве слишком много файлов.")
        name = info.filename
        if not name or "\x00" in name or info.orig_filename != name or info.flag_bits & 1:
            raise _update_error("update_archive_unsafe", "Архив содержит недопустимое имя.")
        is_dir = info.is_dir()
        clean = name[:-1] if is_dir else name
        _archive_parts(clean)
        if clean.split("/")[0].casefold() in {x.casefold() for x in _USER_DIRS}:
            raise _update_error("update_archive_data", "Обновление не должно содержать пользовательские данные.")
        case_key = clean.casefold()
        if case_key in seen:
            raise _update_error("update_archive_duplicate", "Повторное имя в архиве.")
        seen.add(case_key)
        entry_type = stat.S_IFMT(info.external_attr >> 16)
        if entry_type not in (0, stat.S_IFREG, stat.S_IFDIR if is_dir else stat.S_IFREG):
            raise _update_error("update_archive_unsafe", "Недопустимый тип файла в архиве.")
        if info.file_size < 0 or info.file_size > _LIMIT_BYTES:
            raise _update_error("update_archive_too_large", "Файл слишком большой.")
        total += info.file_size
        if total > _LIMIT_BYTES:
            raise _update_error("update_archive_too_large", "Архив слишком большой.")
        entries.append((info, clean))
    actual = {name for info, name in entries if not info.is_dir()}
    if not _REQUIRED_CODE.issubset(actual):
        raise _update_error("update_archive_incomplete", "Не найден обязательный комплект EXE и интерфейса.")
    return entries


def _copy_user_tree(source: Path, target: Path) -> None:
    """Never copy reparse points or the launcher's ephemeral keys/lock files."""
    for top in _USER_DIRS:
        original = source / top
        if not original.exists():
            continue
        if is_redirect(original) or not original.is_dir():
            raise _update_error("update_source_unsafe", "Папка пользовательских данных недопустима.")
        for root, directories, files in os.walk(original, followlinks=False):
            base = Path(root)
            for name in directories:
                if is_redirect(base / name):
                    raise _update_error("update_source_unsafe", "Найдена ссылка в пользовательских данных.")
            relative = base.relative_to(source)
            (target / relative).mkdir(parents=True, exist_ok=True)
            for name in files:
                source_file = base / name
                if is_redirect(source_file) or not source_file.is_file():
                    raise _update_error("update_source_unsafe", "Найдена ссылка или устройство в данных.")
                key = (relative / name).as_posix()
                if key in _RUNTIME_NAMES:
                    continue
                destination = target / relative / name
                shutil.copy2(source_file, destination)


def _verify_directory(root: Path):
    store = JsonStore(root)
    result = store.integrity()
    if not result["ok"]:
        raise _update_error(
            "update_data_invalid",
            "Данные скопированы с нарушением целостности. Старая установка не изменена.",
            409,
        )
    return result["generation"]


def prepare_update(
    source_root: Path,
    new_archive: Path,
    destination_root: Path,
    *,
    expected_sha256: str | None = None,
    progress=None,
) -> dict:
    """Create an independent verified installation without modifying the old one."""
    step = progress or (lambda message: None)
    step("start")
    if Path(source_root).is_symlink() or Path(destination_root).is_symlink():
        raise _update_error("update_destination", "Не используйте ссылки для каталогов установки.")
    if Path(new_archive).is_symlink():
        raise _update_error("update_archive_missing", "Не используйте ссылки на архивы.")
    source = Path(source_root).resolve()
    destination = Path(destination_root).resolve()
    archive_path = Path(new_archive).resolve()
    if (
        destination.exists()
        or destination.is_symlink()
        or destination == source
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise _update_error("update_destination", "Выберите новый каталог вне текущей установки.")
    if (
        not source.is_dir()
        or not (source / "Taverna.exe").is_file()
        or not (source / "data").is_dir()
    ):
        raise _update_error("update_source", "Не найдена запущенная ранее портативная установка.")
    if not archive_path.is_file():
        raise _update_error("update_archive_missing", "Новый ZIP отсутствует.")
    if expected_sha256 is not None and (
        len(expected_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_sha256)
    ):
        raise _update_error("update_checksum", "Ожидалась контрольная сумма SHA256.")

    # Keep the same file handle pinned across hashing, validation and extraction,
    # so replacing the ZIP pathname cannot change what gets published.
    try:
        with archive_path.open("rb") as bundle_file:
            hashed = hashlib.sha256()
            while chunk := bundle_file.read(1024 * 1024):
                hashed.update(chunk)
            actual_sha = hashed.hexdigest()
            if expected_sha256 is not None and actual_sha != expected_sha256:
                raise _update_error("update_checksum", "Контрольная сумма ZIP не совпадает.")
            step("checksum verified")
            bundle_file.seek(0)
            with ZipFile(bundle_file) as bundle:
                entries = _safe_entries(bundle)
                step("ZIP entries verified")
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging = Path(
                    tempfile.mkdtemp(
                        prefix=f".{destination.name}.upgrade-",
                        dir=destination.parent,
                    )
                )
                try:
                    # The main launcher holds this lock for its entire lifetime.
                    # Never migrate a mutable live store.
                    lock_path = source / "data/server.lock"
                    try:
                        with portalocker.Lock(lock_path, timeout=0):
                            step("launcher lock acquired")
                            store = JsonStore(source)
                            backup = create_full_backup(store, reason="pre-update")
                            step("full backup written")
                            for info, clean in entries:
                                output = staging.joinpath(*_archive_parts(clean))
                                if info.is_dir():
                                    output.mkdir(parents=True, exist_ok=True)
                                    continue
                                output.parent.mkdir(parents=True, exist_ok=True)
                                with bundle.open(info) as reader, output.open("xb") as writer:
                                    while chunk := reader.read(1024 * 1024):
                                        writer.write(chunk)
                            step("new code extracted")
                            _copy_user_tree(source, staging)
                            step("user data copied")
                            generation = _verify_directory(staging)
                            step("copied JSON verified")
                            (staging / "upgrade-record.json").write_text(
                                json.dumps(
                                    {
                                        "format": 1,
                                        "zip_sha256": actual_sha,
                                        "source_generation": generation,
                                        "backup_name": backup.name,
                                        "old_install_preserved": True,
                                        "new_install_verified": True,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                            if destination.exists() or destination.is_symlink():
                                raise _update_error(
                                    "update_destination",
                                    "Каталог назначения появился во время обновления.",
                                )
                            os.replace(staging, destination)
                            step("new installation published")
                    except portalocker.exceptions.LockException as exc:
                        raise _update_error(
                            "update_running",
                            "Закройте Таверну перед обновлением. Старая установка не изменена.",
                            409,
                        ) from exc
                    return {
                        "destination": str(destination),
                        "backup": str(backup),
                        "zip_sha256": actual_sha,
                        "data_generation": generation,
                        "old_install_preserved": True,
                    }
                finally:
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
    except (BadZipFile, EOFError, ValueError, OSError) as exc:
        raise _update_error(
            "update_io_error",
            "Не удалось проверить или распаковать ZIP. Старая установка сохранена.",
            503,
        ) from exc

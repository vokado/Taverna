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
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import portalocker

from .core import AppError
from .full_backup import create_full_backup
from .storage import JsonStore, is_redirect, portable_parts

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
    "app/web/dist/index.html",
}


def _update_error(code: str, text: str, status: int = 422) -> AppError:
    return AppError(code, text, status)


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
        portable_parts(clean)
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
) -> dict:
    """Stage and verify a new portable installation; never mutate source code."""
    source = Path(source_root).resolve()
    destination = Path(destination_root).resolve()
    archive_path = Path(new_archive).resolve()
    if (
        destination.exists()
        or destination == source
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise _update_error("update_destination", "Выберите новый каталог вне текущей установки.")
    if not source.is_dir() or not (source / "Taverna.exe").is_file():
        raise _update_error("update_source", "Не найдена действующая портативная установка.")
    if not archive_path.is_file() or archive_path.is_symlink():
        raise _update_error("update_archive_missing", "Новый ZIP отсутствует или является ссылкой.")
    if expected_sha256 is not None and (
        len(expected_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_sha256)
    ):
        raise _update_error("update_checksum", "Ожидалась контрольная сумма SHA256.")
    hashed = hashlib.sha256()
    with archive_path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hashed.update(chunk)
    actual_sha = hashed.hexdigest()
    if expected_sha256 is not None and actual_sha != expected_sha256:
        raise _update_error("update_checksum", "Контрольная сумма ZIP не совпадает.")
    try:
        with ZipFile(archive_path) as bundle:
            entries = _safe_entries(bundle)
    except (BadZipFile, OSError, ValueError, KeyError) as exc:
        raise _update_error("update_archive_invalid", "Архив обновления повреждён.") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.upgrade-", dir=destination.parent))
    try:
        # The same lock is used by the main launcher. A running Taverna must
        # never be copied or updated while its data may be changing.
        lock_path = source / "data/server.lock"
        try:
            with portalocker.Lock(lock_path, timeout=0):
                store = JsonStore(source)
                backup = create_full_backup(store, reason="pre-update")
                with ZipFile(archive_path) as bundle:
                    for info, clean in entries:
                        output = staging.joinpath(*portable_parts(clean))
                        if info.is_dir():
                            output.mkdir(parents=True, exist_ok=True)
                            continue
                        output.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.open(info) as reader, output.open("xb") as writer:
                            while chunk := reader.read(1024 * 1024):
                                writer.write(chunk)
                _copy_user_tree(source, staging)
                generation = _verify_directory(staging)
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
                if destination.exists():
                    raise _update_error("update_destination", "Каталог назначения появился во время обновления.")
                os.replace(staging, destination)
        except portalocker.exceptions.LockException as exc:
            raise _update_error(
                "update_running",
                "Закройте Таверну перед обновлением. Действующая установка не изменена.",
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

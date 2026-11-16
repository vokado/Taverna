"""Consistent, portable backups of published JSON and immutable user assets.

Format 2 is intentionally separate from migration-only JSON checkpoints.
No runtime secrets, logs, caches outside the published snapshot, or application
binaries are copied. Restore is offline into a *new* directory only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .core import AppError
from .storage import JsonStore, digest, encoded, portable_parts, safe_path

MANIFEST = "backup-manifest.json"
MAX_ENTRIES = 100_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _invalid() -> AppError:
    return AppError("invalid_full_backup", "Архив резервной копии повреждён или имеет недопустимый состав.", 422)


def _assets(snapshot: dict[str, bytes]) -> dict[str, str]:
    """Select only files referenced by the same published JSON generation."""
    assets: dict[str, str] = {}
    for name, raw in snapshot.items():
        if name == "data/config/store-manifest.json":
            continue
        value = json.loads(raw)
        kind, payload = value.get("kind"), value.get("payload", {})
        if kind in ("media_asset", "material"):
            path, checksum = payload.get("relative_path"), payload.get("checksum")
            if not isinstance(path, str) or not path.startswith(("media/", "library/")):
                raise AppError("backup_source_invalid", "У медиаресурса недопустимый путь.", 409)
            pairs = [(path, checksum)]
        elif kind == "plugin_registration":
            directory = payload.get("directory")
            files = payload.get("files")
            if not isinstance(directory, str) or not directory.startswith("plugins/") or not isinstance(files, dict):
                raise AppError("backup_source_invalid", "Манифест расширения повреждён.", 409)
            pairs = [(directory + "/" + relative, checksum) for relative, checksum in files.items()]
        else:
            continue
        for path, checksum in pairs:
            if not isinstance(path, str) or not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
                raise AppError("backup_source_invalid", "Некорректный путь или контрольная сумма ресурса.", 409)
            try:
                portable_parts(path)
            except AppError as exc:
                raise AppError("backup_source_invalid", "Недопустимое имя файла резервной копии.", 409) from exc
            if path in assets and assets[path] != checksum:
                raise AppError("backup_source_invalid", "Разные записи ссылаются на различное содержимое одного файла.", 409)
            assets[path] = checksum
    return assets


def create_full_backup(
    store: JsonStore, *, reason: str = "automatic", destination_dir: Path | None = None
) -> Path:
    """Create a verified ZIP; a failed copy never replaces an existing archive.

    The store snapshot is atomic. Referenced binary objects are content addressed
    and validated against that snapshot before publishing the backup.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", reason):
        raise AppError("invalid_backup_reason", "Недопустимое название резервной копии.", 422)
    snapshot = store.snapshot()
    assets = _assets(snapshot)
    folder = Path(destination_dir) if destination_dir is not None else store.root / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"taverna-{reason}-{stamp}-{uuid4().hex}.zip"
    temporary = folder / f".{target.name}.tmp"
    checksums = {name: digest(data) for name, data in snapshot.items()}
    try:
        with temporary.open("xb") as stream:
            with ZipFile(stream, "w", ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                for name, data in snapshot.items():
                    archive.writestr(name, data)
                for name, expected in sorted(assets.items()):
                    path = safe_path(store.root, name)
                    try:
                        raw = path.read_bytes()
                    except OSError as exc:
                        raise AppError("backup_asset_missing", "Файл материала отсутствует или недоступен.", 503) from exc
                    if digest(raw) != expected:
                        raise AppError("backup_asset_corrupt", "Контрольная сумма материала не совпадает.", 503)
                    archive.writestr(name, raw)
                    checksums[name] = expected
                archive.writestr(
                    MANIFEST,
                    encoded(
                        {
                            "format": 2,
                            "id": str(uuid4()),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "reason": reason,
                            "generation": json.loads(snapshot["data/config/store-manifest.json"])["generation"],
                            "files": checksums,
                        }
                    ),
                )
            stream.flush()
            os.fsync(stream.fileno())
        verify_full_backup(temporary)
        os.replace(temporary, target)
        return target
    except OSError as exc:
        raise AppError("backup_io_error", "Не удалось сохранить резервную копию на диск.", 503) from exc
    finally:
        temporary.unlink(missing_ok=True)


def verify_full_backup(path: Path) -> dict:
    """Verify names, types, published object graph, sizes and every SHA-256."""
    try:
        with ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES + 1 or len({i.filename for i in infos}) != len(infos):
                raise _invalid()
            for info in infos:
                if info.is_dir() or info.orig_filename != info.filename or info.flag_bits & 1:
                    raise _invalid()
                if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise _invalid()
                if info.filename != MANIFEST:
                    portable_parts(info.filename)
                    if not info.filename.startswith(("data/", "media/", "library/", "plugins/")):
                        raise _invalid()
            meta = archive.getinfo(MANIFEST)
            if meta.file_size > MAX_MANIFEST_BYTES:
                raise _invalid()
            manifest = json.loads(archive.read(MANIFEST))
            files = manifest.get("files")
            if (
                manifest.get("format") != 2
                or not isinstance(files, dict)
                or "data/config/store-manifest.json" not in files
                or set(files) | {MANIFEST} != {i.filename for i in infos}
            ):
                raise _invalid()
            published = json.loads(archive.read("data/config/store-manifest.json"))
            if published.get("generation") != manifest.get("generation"):
                raise _invalid()
            # Compare against the graph of *published* JSON and its referenced
            # immutable assets. Neither a missing media file nor an unrelated
            # extra executable may be silently accepted during restore.
            document_data = {}
            for entry in published["entries"].values():
                name = "data/" + entry["object"]
                if files.get(name) != entry["sha256"]:
                    raise _invalid()
                document_data[name] = archive.read(name)
            expected_assets = _assets(document_data)
            if set(files) != {"data/config/store-manifest.json", *document_data, *expected_assets}:
                raise _invalid()
            if any(files[name] != sha for name, sha in expected_assets.items()):
                raise _invalid()
            total = 0
            for info in infos:
                if info.filename == MANIFEST:
                    continue
                checksum = files.get(info.filename)
                if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
                    raise _invalid()
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise _invalid()
                hashed = hashlib.sha256()
                size = 0
                with archive.open(info) as item:
                    while chunk := item.read(1024 * 1024):
                        size += len(chunk)
                        if size > info.file_size:
                            raise _invalid()
                        hashed.update(chunk)
                if size != info.file_size or hashed.hexdigest() != checksum:
                    raise _invalid()
            return manifest
    except (BadZipFile, KeyError, TypeError, ValueError, OSError, AppError) as exc:
        if isinstance(exc, AppError) and exc.code == "invalid_full_backup":
            raise
        raise _invalid() from exc


def restore_full_backup_to_new_directory(archive_path: Path, target_root: Path) -> Path:
    """Offline recovery into a previously non-existent directory; never overwrite user data."""
    manifest = verify_full_backup(archive_path)
    target_root = Path(target_root).absolute()
    if target_root.exists():
        raise AppError("restore_destination_exists", "Выберите новый пустой каталог для восстановления.", 409)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    staging = target_root.with_name(f".{target_root.name}.{uuid4().hex}.restore")
    try:
        staging.mkdir()
        with ZipFile(archive_path) as archive:
            for name in manifest["files"]:
                output = safe_path(staging, name)
                output.parent.mkdir(parents=True, exist_ok=True)
                temp = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
                try:
                    with archive.open(name) as source, temp.open("xb") as sink:
                        # The ZIP could have been replaced after initial verification.
                        # Recheck extracted bytes before any restored file is published.
                        hashed = hashlib.sha256()
                        while chunk := source.read(1024 * 1024):
                            hashed.update(chunk)
                            sink.write(chunk)
                        if hashed.hexdigest() != manifest["files"][name]:
                            raise _invalid()
                        sink.flush()
                        os.fsync(sink.fileno())
                    os.replace(temp, output)
                finally:
                    temp.unlink(missing_ok=True)
        if target_root.exists():
            raise AppError("restore_destination_exists", "Каталог восстановления появился во время операции.", 409)
        os.replace(staging, target_root)
        return target_root
    finally:
        if staging.exists():
            shutil.rmtree(staging)

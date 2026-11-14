import json
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from .core import AppError
from .storage import JsonStore, Mutation, atomic_write, digest, document, encoded


def checkpoint(store: JsonStore, reason: str) -> Path:
    files = store.snapshot()
    identifier = str(uuid4())
    target = store.root / "backups" / f"{reason}-{identifier}.zip"
    temporary = target.with_suffix(".tmp")
    manifest = {
        "format": 1,
        "id": identifier,
        "reason": reason,
        "files": {k: digest(v) for k, v in files.items()},
    }
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for path, data in files.items():
                archive.writestr(path, data)
            archive.writestr("backup-manifest.json", encoded(manifest))
        verify_checkpoint(temporary)
        atomic_write(target, temporary.read_bytes())
    finally:
        temporary.unlink(missing_ok=True)
    return target


def verify_checkpoint(path: Path) -> dict:
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise AppError("invalid_backup", "Контрольная копия повреждена.")
        manifest = json.loads(archive.read("backup-manifest.json"))
        if set(archive.namelist()) != set(manifest["files"]) | {"backup-manifest.json"}:
            raise AppError("invalid_backup", "Состав контрольной копии не совпадает с манифестом.")
        for name, checksum in manifest["files"].items():
            if digest(archive.read(name)) != checksum:
                raise AppError("invalid_backup", "Контрольная сумма копии не совпадает.")
        return manifest


def migrate(
    store: JsonStore, kind: str, source_version: int, target_version: int, transform: Callable[[dict], dict]
) -> dict:
    if target_version <= source_version:
        raise AppError("invalid_migration", "Версия миграции должна возрастать.")
    rows, _ = store.scan("")
    matching = [
        r for r in rows if r["value"]["kind"] == kind and r["value"]["schema_version"] == source_version
    ]
    if not matching:
        return {"migrated": 0, "backup": None}
    backup = checkpoint(store, "before-migration")
    changes = []
    for row in matching:
        value = row["value"]
        value["payload"] = transform(json.loads(encoded(value["payload"])))
        value["schema_version"] = target_version
        store.validate(value)
        changes.append(Mutation(row["key"], value, value["revision"]))
    log = document(
        "migration_log",
        {
            "kind": kind,
            "source_version": source_version,
            "target_version": target_version,
            "backup": backup.name,
            "keys": [change.key for change in changes],
            "status": "complete",
        },
    )
    changes.append(Mutation(f"config/migrations/{log['id']}.json", log, 0))
    store.transaction(changes)
    return {"migrated": len(matching), "backup": backup.name}

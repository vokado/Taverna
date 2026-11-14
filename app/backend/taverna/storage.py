"""Versioned JSON store: immutable objects, one publication pointer, durable intent log.

All paths in manifests are portable. Acknowledgement follows commit marker, publication
and completion. Concurrent readers/writers use the same cross-process lock.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import UUID, uuid4

import jsonschema
import portalocker

from .core import AppError

BASE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id", "kind", "schema_version", "revision", "created_at", "updated_at", "payload"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "kind": {"type": "string", "minLength": 1},
        "schema_version": {"type": "integer", "minimum": 1},
        "revision": {"type": "integer", "minimum": 1},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "payload": {"type": "object"},
        "refs": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
BASE_VALIDATOR = jsonschema.Draft202012Validator(BASE_SCHEMA, format_checker=jsonschema.FormatChecker())

CATEGORIES = (
    "characters",
    "chats",
    "worlds",
    "scenarios",
    "memory",
    "relationships",
    "config",
    "indexes",
    "presets",
    "automations",
    "transactions",
    "manuscripts",
    "media",
    "jobs",
    "events",
)


def encoded(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_path(root: Path, key: str) -> Path:
    if "\\" in key or ":" in key or not key or PurePosixPath(key).is_absolute():
        raise AppError("invalid_path", "Недопустимый путь.")
    parts = key.split("/")
    if any(
        p in ("", ".", "..")
        or p.endswith((" ", "."))
        or not re.fullmatch(r"[\w.\-]+", p)
        or p.split(".")[0].upper()
        in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
        for p in parts
    ):
        raise AppError("invalid_path", "Недопустимый путь.")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AppError("invalid_path", "Ссылки за пределы каталога запрещены.")
        if current.exists():
            attributes = getattr(current.stat(), "st_file_attributes", 0)
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise AppError("invalid_path", "Перенаправления файловой системы запрещены.")
    if not current.resolve().is_relative_to(root.resolve()):
        raise AppError("invalid_path", "Путь выходит за каталог программы.")
    return current


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def document(kind: str, payload: dict, *, entity_id: str | None = None, refs=()) -> dict:
    stamp = now()
    return {
        "id": entity_id or str(uuid4()),
        "kind": kind,
        "schema_version": 1,
        "revision": 1,
        "created_at": stamp,
        "updated_at": stamp,
        "payload": payload,
        "refs": list(refs),
    }


@dataclass(frozen=True)
class Mutation:
    key: str
    value: dict | None
    expected_revision: int


class ReadView:
    def __init__(self, store, manifest):
        self.store, self.manifest = store, manifest

    def get(self, key: str) -> dict | None:
        self.store._logical(key)
        entry = self.manifest["entries"].get(key)
        return self.store._read(entry) if entry else None


class JsonStore:
    def __init__(self, root: Path, *, fault: Callable[[str], None] | None = None):
        self.root = root.resolve()
        self.data = self.root / "data"
        self._thread_lock = threading.RLock()
        self.fault = fault or (lambda phase: None)
        self.schemas: dict[tuple[str, int], jsonschema.Draft202012Validator] = {}
        for category in CATEGORIES:
            safe_path(self.root, f"data/{category}").mkdir(parents=True, exist_ok=True)
        for folder in ("media", "library", "plugins", "backups", "logs"):
            safe_path(self.root, folder).mkdir(exist_ok=True)
        self.pointer = self.data / "config/store-manifest.json"
        self.journal = self.data / "transactions"
        with self.locked():
            if not self.pointer.exists():
                if any(self.journal.glob("*/commit.json")):
                    raise AppError(
                        "manifest_corrupt", "Манифест утрачен. Восстановите контрольную копию.", 503
                    )
                atomic_write(
                    self.pointer,
                    encoded({"format": 1, "generation": 0, "transaction_id": None, "entries": {}}),
                )
            self._recover()
            self._manifest()

    @contextmanager
    def locked(self):
        with self._thread_lock:
            with portalocker.Lock(self.data / "config/manager.lock", timeout=10):
                yield

    def register(self, kind: str, version: int, schema: dict):
        jsonschema.Draft202012Validator.check_schema(schema)
        self.schemas[kind, version] = jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        )

    def validate(self, value: dict):
        try:
            BASE_VALIDATOR.validate(value)
            schema = self.schemas.get((value["kind"], value["schema_version"]))
            if schema is not None:
                schema.validate(value["payload"])
            elif any(kind == value["kind"] for kind, version in self.schemas):
                raise AppError("unsupported_schema", "Версия схемы не поддерживается.", 409)
        except (jsonschema.ValidationError, ValueError, TypeError) as exc:
            raise AppError("invalid_document", "Документ не соответствует схеме.", 422) from exc

    def _logical(self, key: str):
        safe_path(self.data, key)
        if (
            key.split("/")[0] not in CATEGORIES
            or not key.endswith(".json")
            or key.startswith(("transactions/", "config/store-", "config/manager"))
        ):
            raise AppError("invalid_path", "Недопустимый путь документа.")

    def _validate_manifest(self, value):
        if (
            not isinstance(value, dict)
            or value.get("format") != 1
            or not isinstance(value.get("generation"), int)
            or value["generation"] < 0
            or not isinstance(value.get("entries"), dict)
        ):
            raise AppError("manifest_corrupt", "Манифест хранилища повреждён. Требуется восстановление.", 503)
        for key, entry in value["entries"].items():
            self._logical(key)
            if (
                not isinstance(entry, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))
                or not isinstance(entry.get("revision"), int)
            ):
                raise AppError("manifest_corrupt", "Запись манифеста повреждена.", 503)
            safe_path(self.data, entry["object"])
            if not entry["object"].endswith("." + entry["sha256"] + ".json"):
                raise AppError("manifest_corrupt", "Недопустимое имя объекта хранилища.", 503)
        return value

    def _manifest(self):
        try:
            return self._validate_manifest(json.loads(self.pointer.read_bytes()))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AppError("manifest_corrupt", "Манифест хранилища недоступен или повреждён.", 503) from exc

    def _read(self, entry):
        try:
            raw = safe_path(self.data, entry["object"]).read_bytes()
            if digest(raw) != entry["sha256"]:
                raise ValueError("checksum")
            value = json.loads(raw)
            self.validate(value)
            if value["revision"] != entry["revision"]:
                raise ValueError("revision")
            return value
        except (OSError, ValueError, KeyError) as exc:
            raise AppError("document_corrupt", "Документ повреждён или отсутствует.", 503) from exc

    def _recover(self):
        current = self._manifest()
        # Directory name is sortable generation + UUID. Only committed, incomplete intents roll forward.
        for directory in sorted(self.journal.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            if not (directory / "commit.json").exists() or (directory / "complete.json").exists():
                continue
            try:
                raw = (directory / "intent.json").read_bytes()
                marker = json.loads((directory / "commit.json").read_bytes())
                if digest(raw) != marker["intent_sha256"]:
                    raise ValueError("intent checksum")
                intent = json.loads(raw)
                after = self._validate_manifest(intent["after"])
                if after["generation"] > current["generation"]:
                    if intent["before_sha256"] != digest(encoded(current)):
                        raise ValueError("generation mismatch")
                    for key in intent["changed"]:
                        if key in after["entries"]:
                            self._read(after["entries"][key])
                    atomic_write(self.pointer, encoded(after))
                    current = after
                atomic_write(directory / "complete.json", encoded({"recovered": True, "at": now()}))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise AppError(
                    "transaction_recovery_required", "Незавершённая транзакция требует восстановления.", 503
                ) from exc

    def get(self, key: str) -> dict | None:
        self._logical(key)
        with self.locked():
            self._recover()
            entry = self._manifest()["entries"].get(key)
            return self._read(entry) if entry else None

    @contextmanager
    def view(self):
        """A bounded, consistent read view. Callers must not mutate within this scope."""
        with self.locked():
            self._recover()
            yield ReadView(self, self._manifest())

    def scan(self, prefix: str, *, tolerate_corrupt=False) -> tuple[list[dict], list[str]]:
        with self.locked():
            self._recover()
            result, errors = [], []
            for key, entry in self._manifest()["entries"].items():
                if key.startswith(prefix):
                    try:
                        result.append({"key": key, "value": self._read(entry)})
                    except AppError:
                        if not tolerate_corrupt:
                            raise
                        errors.append(key)
            return result, errors

    def transaction(self, changes: list[Mutation], *, transaction_id: str | None = None) -> dict:
        try:
            return self._transaction(changes, transaction_id=transaction_id)
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise AppError(
                    "storage_full", "На диске недостаточно места. Освободите место и повторите.", 507
                ) from exc
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise AppError(
                    "storage_denied", "Нет прав на запись или файл занят другой программой.", 503
                ) from exc
            raise AppError(
                "storage_io_error", "Ошибка диска. Проверьте носитель и журнал восстановления.", 503
            ) from exc

    def _transaction(self, changes: list[Mutation], *, transaction_id: str | None = None) -> dict:
        if not changes or len({c.key for c in changes}) != len(changes):
            raise AppError("invalid_transaction", "Транзакция пуста или содержит повторные пути.")
        txid = transaction_id or str(uuid4())
        try:
            UUID(txid)
        except ValueError as exc:
            raise AppError("invalid_transaction", "Недопустимый идентификатор транзакции.") from exc
        fingerprint = digest(
            encoded([{"key": c.key, "value": c.value, "expected": c.expected_revision} for c in changes])
        )
        with self.locked():
            self._recover()
            before = self._manifest()
            # Repeated transaction must match exactly; a reused id is never a new write.
            for directory in self.journal.glob(f"*-{txid}"):
                intent = json.loads((directory / "intent.json").read_bytes())
                if intent["fingerprint"] != fingerprint:
                    raise AppError(
                        "idempotency_conflict", "Идентификатор уже использован для другой операции.", 409
                    )
                if (directory / "commit.json").exists():
                    return intent["after"]
            after = json.loads(encoded(before))
            after.update(generation=before["generation"] + 1, transaction_id=txid)
            for change in changes:
                self._logical(change.key)
                previous = before["entries"].get(change.key)
                revision = previous["revision"] if previous else 0
                if revision != change.expected_revision:
                    raise AppError(
                        "storage_conflict",
                        "Данные изменились в другой вкладке. Обновите запись.",
                        409,
                        current_revision=revision,
                        key=change.key,
                    )
                if change.value is None:
                    after["entries"].pop(change.key, None)
                    continue
                value = json.loads(encoded(change.value))
                value["revision"] = revision + 1
                value["updated_at"] = now()
                if previous:
                    old = self._read(previous)
                    if value["id"] != old["id"] or value["kind"] != old["kind"]:
                        raise AppError("identity_change", "Идентификатор и тип записи нельзя изменять.", 409)
                    value["created_at"] = old["created_at"]
                self.validate(value)
                raw = encoded(value)
                checksum = digest(raw)
                logical = PurePosixPath(change.key)
                physical = str(logical.with_name(f"{logical.stem}.{checksum}.json"))
                atomic_write(safe_path(self.data, physical), raw)
                after["entries"][change.key] = {
                    "object": physical,
                    "sha256": checksum,
                    "revision": revision + 1,
                    "refs": value.get("refs", []),
                }
                self.fault("object_written")
            # Validate every reference in the resulting snapshot, including inbound references to deleted entities.
            for entry in after["entries"].values():
                for reference in entry.get("refs", []):
                    if reference not in after["entries"]:
                        raise AppError(
                            "broken_reference",
                            "Операция оставляет несуществующую ссылку.",
                            409,
                            key=reference,
                        )
            directory = self.journal / f"{after['generation']:016}-{txid}"
            directory.mkdir(exist_ok=True)
            intent = {
                "before_sha256": digest(encoded(before)),
                "after": after,
                "changed": [c.key for c in changes],
                "fingerprint": fingerprint,
            }
            raw_intent = encoded(intent)
            atomic_write(directory / "intent.json", raw_intent)
            self.fault("prepared")
            atomic_write(directory / "commit.json", encoded({"intent_sha256": digest(raw_intent)}))
            self.fault("committed")
            atomic_write(self.pointer, encoded(after))
            self.fault("published")
            atomic_write(directory / "complete.json", encoded({"at": now()}))
            self.fault("complete")
            return after

    def snapshot(self) -> dict[str, bytes]:
        with self.locked():
            self._recover()
            manifest = self._manifest()
            result = {"data/config/store-manifest.json": encoded(manifest)}
            for entry in manifest["entries"].values():
                self._read(entry)
                result["data/" + entry["object"]] = safe_path(self.data, entry["object"]).read_bytes()
            return result

    def rebuild_index(self, name: str, prefix: str, project: Callable[[dict], dict]) -> dict:
        key = f"indexes/{name}.json"
        self._logical(key)
        with self.view() as view:
            rows = [
                {"key": k, "projection": project(view.get(k))}
                for k in view.manifest["entries"]
                if k.startswith(prefix) and not k.startswith("indexes/")
            ]
            existing = view.manifest["entries"].get(key)
            expected = existing["revision"] if existing else 0
            old_id = None
            if existing:
                try:
                    old_id = view.get(key)["id"]
                except AppError:
                    pass
        if existing and old_id is None:
            self.transaction([Mutation(key, None, expected)])
            expected = 0
        value = document("derived_index", {"source_prefix": prefix, "rows": rows}, entity_id=old_id)
        self.transaction([Mutation(key, value, expected)])
        return self.get(key)

    def integrity(self) -> dict:
        with self.locked():
            self._recover()
            manifest, errors = self._manifest(), []
            for key, entry in manifest["entries"].items():
                try:
                    value = self._read(entry)
                    for reference in value.get("refs", []):
                        if reference not in manifest["entries"]:
                            errors.append({"key": key, "code": "broken_reference"})
                except AppError as exc:
                    errors.append({"key": key, "code": exc.code})
            return {
                "generation": manifest["generation"],
                "documents": len(manifest["entries"]),
                "ok": not errors,
                "errors": errors,
            }

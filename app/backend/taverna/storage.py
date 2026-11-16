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
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise AppError(
            "invalid_json_data",
            "Данные должны содержать корректный UTF-8 JSON без нечисловых значений NaN/Infinity.",
            422,
        ) from exc


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_redirect(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in (*range(10), "¹", "²", "³")),
        *(f"LPT{i}" for i in (*range(10), "¹", "²", "³")),
    }
)


def portable_parts(key: str) -> list[str]:
    if "\\" in key or ":" in key or not key or PurePosixPath(key).is_absolute():
        raise AppError("invalid_path", "Недопустимый путь.")
    parts = key.split("/")
    if any(
        p in ("", ".", "..")
        or p.endswith((" ", "."))
        or not re.fullmatch(r"[\w.\-]+", p)
        or p.split(".")[0].upper() in _WINDOWS_RESERVED
        for p in parts
    ):
        raise AppError("invalid_path", "Недопустимый путь.")
    return parts


def safe_path(root: Path, key: str) -> Path:
    parts = portable_parts(key)
    current = root
    for part in parts:
        current = current / part
        if is_redirect(current):
            raise AppError("invalid_path", "Ссылки и перенаправления файловой системы запрещены.")
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
                if any(self.journal.glob("*/commit.json")) or any(
                    self.journal.glob("history/*/*/commit.json")
                ):
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

    def _logical(self, key: str, *, check_path=True):
        if check_path:
            safe_path(self.data, key)
        else:
            portable_parts(key)
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
            self._logical(key, check_path=False)
            if (
                not isinstance(entry, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))
                or not isinstance(entry.get("revision"), int)
            ):
                raise AppError("manifest_corrupt", "Запись манифеста повреждена.", 503)
            portable_parts(entry["object"])
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

    def _receipt(self, directory):
        raw = safe_path(
            self.data, str((directory / "intent.json").relative_to(self.data)).replace("\\", "/")
        ).read_bytes()
        intent = json.loads(raw)
        if not isinstance(intent, dict):
            raise ValueError("intent object required")
        marker = safe_path(
            self.data, str((directory / "commit.json").relative_to(self.data)).replace("\\", "/")
        )
        if marker.exists() and digest(raw) != json.loads(marker.read_bytes())["intent_sha256"]:
            raise ValueError("intent checksum")
        generation = int(directory.name[:16])
        txid = str(UUID(directory.name[17:]))
        if intent.get("format", 1) == 1:
            if (
                intent["after"]["generation"] != generation
                or str(UUID(intent["after"]["transaction_id"])) != txid
            ):
                raise ValueError("receipt identity")
        elif (
            intent.get("format") != 2
            or intent["generation"] != generation
            or str(UUID(intent["transaction_id"])) != txid
        ):
            raise ValueError("unsupported journal or receipt identity")
        return intent

    def _apply_intent(self, intent, before):
        if intent.get("format", 1) == 1:
            return self._validate_manifest(intent["after"])
        if intent["generation"] != before["generation"] + 1 or intent["before_sha256"] != digest(
            encoded(before)
        ):
            raise ValueError("delta base mismatch")
        after = {
            **before,
            "generation": intent["generation"],
            "transaction_id": intent["transaction_id"],
            "entries": dict(before["entries"]),
        }
        if set(intent["updated"]) != set(intent["changed"]):
            raise ValueError("delta keys mismatch")
        for key, value in intent["updated"].items():
            if value is None:
                after["entries"].pop(key, None)
            else:
                after["entries"][key] = value
        self._validate_manifest(after)
        if digest(encoded(after)) != intent["after_sha256"]:
            raise ValueError("delta result checksum")
        return after

    def _archive_complete(self, directory):
        txid = str(UUID(directory.name[17:]))
        target = safe_path(self.data, f"transactions/history/{txid[:2]}/{directory.name}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(directory, target)
        except OSError:
            # An antivirus/Windows sharing lock may defer this optimization;
            # complete remains authoritative in the active directory.
            return
        if os.name != "nt":
            for parent in (self.journal, target.parent):
                fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

    def _checkpoint(self, before):
        folder = safe_path(self.data, "transactions/checkpoints")
        candidates = []
        if folder.exists():
            for path in folder.iterdir():
                match = re.fullmatch(r"(\d{16})\.[a-f0-9]{64}\.json", path.name)
                if match and int(match[1]) <= before["generation"]:
                    candidates.append((int(match[1]), path.name))
        latest = max(candidates) if candidates else None
        if latest is None or before["generation"] - latest[0] >= 128:
            raw = encoded(before)
            name = f"{before['generation']:016}.{digest(raw)}.json"
            atomic_write(safe_path(self.data, "transactions/checkpoints/" + name), raw)
            return name
        return latest[1]

    def _historical_manifest(self, intent):
        if intent.get("format", 1) == 1:
            return self._validate_manifest(intent["after"])
        name = intent["checkpoint"]
        if not re.fullmatch(r"\d{16}\.[a-f0-9]{64}\.json", name):
            raise ValueError("checkpoint name")
        raw = safe_path(self.data, "transactions/checkpoints/" + name).read_bytes()
        if digest(raw) != name.split(".")[1]:
            raise ValueError("checkpoint checksum")
        current = self._validate_manifest(json.loads(raw))
        receipts = []
        history = safe_path(self.data, "transactions/history")
        if history.exists():
            for bucket in history.iterdir():
                if not is_redirect(bucket) and bucket.is_dir():
                    receipts.extend(bucket.iterdir())
        receipts.extend(self.journal.iterdir())
        selected = []
        for directory in receipts:
            match = re.fullmatch(r"(\d{16})-[a-fA-F0-9-]{32,36}", directory.name)
            if (
                match
                and current["generation"] < int(match[1]) <= intent["generation"]
                and not is_redirect(directory)
            ):
                if (directory / "commit.json").exists():
                    selected.append(directory)
        for directory in sorted(selected, key=lambda d: d.name):
            delta = self._receipt(directory)
            if delta["before_sha256"] != digest(encoded(current)):
                raise ValueError("historical generation chain")
            current = self._apply_intent(delta, current)
        if (
            current["generation"] != intent["generation"]
            or digest(encoded(current)) != intent["after_sha256"]
        ):
            raise ValueError("historical manifest mismatch")
        return current

    def _recover(self):
        current = self._manifest()
        # Only active intents need recovery. Completed receipts remain JSON in
        # history for idempotency and audit, but are never rescanned per read.
        for directory in sorted(self.journal.iterdir()):
            if (
                directory.name in ("history", "checkpoints")
                or is_redirect(directory)
                or not directory.is_dir()
            ):
                continue
            if not (directory / "commit.json").exists():
                continue
            try:
                if (directory / "complete.json").exists():
                    self._archive_complete(directory)
                    continue
                intent = self._receipt(directory)
                generation = (
                    intent["after"]["generation"] if intent.get("format", 1) == 1 else intent["generation"]
                )
                if generation > current["generation"]:
                    if intent["before_sha256"] != digest(encoded(current)):
                        raise ValueError("generation mismatch")
                    after = self._apply_intent(intent, current)
                    for key in intent["changed"]:
                        if key in after["entries"]:
                            self._read(after["entries"][key])
                    atomic_write(self.pointer, encoded(after))
                    current = after
                elif (
                    generation == current["generation"]
                    and intent.get("format") == 2
                    and intent["after_sha256"] != digest(encoded(current))
                ):
                    raise ValueError("published delta checksum")
                atomic_write(directory / "complete.json", encoded({"recovered": True, "at": now()}))
                self._archive_complete(directory)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise AppError(
                    "transaction_recovery_required", "Незавершённая транзакция требует восстановления.", 503
                ) from exc
        return current

    def get(self, key: str) -> dict | None:
        self._logical(key)
        with self.locked():
            manifest = self._recover()
            entry = manifest["entries"].get(key)
            return self._read(entry) if entry else None

    @contextmanager
    def view(self):
        """A bounded, consistent read view. Callers must not mutate within this scope."""
        with self.locked():
            manifest = self._recover()
            yield ReadView(self, manifest)

    def scan(self, prefix: str, *, tolerate_corrupt=False) -> tuple[list[dict], list[str]]:
        with self.locked():
            manifest = self._recover()
            result, errors = [], []
            for key, entry in manifest["entries"].items():
                if key.startswith(prefix):
                    try:
                        result.append({"key": key, "value": self._read(entry)})
                    except AppError:
                        if not tolerate_corrupt:
                            raise
                        errors.append(key)
            return result, errors

    def transaction(
        self,
        changes: list[Mutation],
        *,
        transaction_id: str | None = None,
        guards: dict[str, int] | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> dict:
        try:
            return self._transaction(
                changes, transaction_id=transaction_id, guards=guards, before_commit=before_commit
            )
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

    def _transaction(
        self,
        changes: list[Mutation],
        *,
        transaction_id: str | None = None,
        guards: dict[str, int] | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> dict:
        if not changes or len({c.key for c in changes}) != len(changes):
            raise AppError("invalid_transaction", "Транзакция пуста или содержит повторные пути.")
        txid = transaction_id or str(uuid4())
        try:
            txid = str(UUID(txid))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AppError("invalid_transaction", "Недопустимый идентификатор транзакции.") from exc
        fingerprint = digest(
            encoded([{"key": c.key, "value": c.value, "expected": c.expected_revision} for c in changes])
        )
        if guards:
            fingerprint = digest(encoded({"changes": fingerprint, "guards": guards}))
        with self.locked():
            self._recover()
            before = self._manifest()
            # Look up a receipt by UUID bucket without scanning every completed
            # transaction. Legacy active receipts are handled by recovery above.
            bucket = safe_path(self.data, f"transactions/history/{txid[:2]}")
            directories = list(self.journal.iterdir())
            if bucket.exists():
                directories.extend(bucket.iterdir())
            for directory in directories:
                if directory.name[17:].lower().replace("-", "") != txid.replace("-", ""):
                    continue
                if not (directory / "intent.json").exists():
                    continue
                try:
                    intent = self._receipt(directory)
                    if intent["fingerprint"] != fingerprint:
                        raise AppError(
                            "idempotency_conflict", "Идентификатор уже использован для другой операции.", 409
                        )
                    if (directory / "commit.json").exists():
                        return self._historical_manifest(intent)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise AppError(
                        "transaction_recovery_required",
                        "Журнал повторной операции повреждён; требуется восстановление.",
                        503,
                    ) from exc
            for key, expected in (guards or {}).items():
                self._logical(key)
                actual = before["entries"].get(key, {}).get("revision", 0)
                if actual != expected:
                    raise AppError(
                        "storage_conflict",
                        "Источник операции изменился. Обновите предпросмотр.",
                        409,
                        key=key,
                        current_revision=actual,
                    )
            if before_commit:
                before_commit()
            after = {
                **before,
                "entries": dict(before["entries"]),
                "generation": before["generation"] + 1,
                "transaction_id": txid,
            }
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
            after_raw = encoded(after)
            common = {
                "before_sha256": digest(encoded(before)),
                "changed": [c.key for c in changes],
                "fingerprint": fingerprint,
            }
            intent = {
                **common,
                "format": 2,
                "generation": after["generation"],
                "transaction_id": txid,
                "after_sha256": digest(after_raw),
                "updated": {c.key: after["entries"].get(c.key) for c in changes},
                "checkpoint": "0" * 16 + "." + "0" * 64 + ".json",
            }
            # Retain the existing v1 representation where its full manifest is
            # smaller than a delta header (e.g. repeated edits to one tiny key).
            # Both representations have the same commit/recovery protocol.
            full_bytes = len(after_raw) + len(encoded({**common, "after": None})) - 4
            if full_bytes <= len(encoded(intent)):
                intent = {**common, "after": after}
            else:
                intent["checkpoint"] = self._checkpoint(before)
            raw_intent = encoded(intent)
            atomic_write(directory / "intent.json", raw_intent)
            self.fault("prepared")
            if before_commit:
                before_commit()
            atomic_write(directory / "commit.json", encoded({"intent_sha256": digest(raw_intent)}))
            self.fault("committed")
            atomic_write(self.pointer, after_raw)
            self.fault("published")
            atomic_write(directory / "complete.json", encoded({"at": now()}))
            self.fault("complete")
            self._archive_complete(directory)
            self.fault("archived")
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

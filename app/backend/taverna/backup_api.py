"""Owner-only HTTP entrypoints for full local backup creation and verification."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .core import AppError
from .full_backup import create_full_backup, verify_full_backup
from .storage import JsonStore

FILE_NAME = re.compile(r"taverna-[A-Za-z0-9_-]{1,40}-\d{8}T\d{6}Z-[0-9a-f]{32}\.zip\Z")


class BackupRequest(BaseModel):
    reason: str = Field(default="manual", pattern=r"^[A-Za-z0-9_-]{1,40}$")


class BackupService:
    def __init__(self, store: JsonStore):
        self.store = store
        self.directory = store.root / "backups"
        self._creation_lock = threading.Lock()

    def _file(self, name: str) -> Path:
        if not FILE_NAME.fullmatch(name):
            raise AppError("backup_invalid_name", "Недопустимое имя архивной копии.", 422)
        path = self.directory / name
        if path.is_symlink() or not path.is_file():
            raise AppError("backup_missing", "Резервная копия не найдена.", 404)
        return path

    def list(self):
        files = []
        for path in self.directory.glob("taverna-*.zip"):
            if not FILE_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            stats = path.stat()
            files.append({"name": path.name, "bytes": stats.st_size, "modified_at": stats.st_mtime})
        return {"items": sorted(files, key=lambda x: x["name"], reverse=True)}

    def create(self, reason: str):
        with self._creation_lock:
            path = create_full_backup(self.store, reason=reason)
        return {"name": path.name, "bytes": path.stat().st_size}

    def _automatic_files(self) -> list[tuple[datetime, Path]]:
        result = []
        for path in self.directory.glob("taverna-automatic-*.zip"):
            if not FILE_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            try:
                stamp = path.name.split("-")[2]
                date = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            result.append((date, path))
        return sorted(result, key=lambda item: (item[0], item[1].name), reverse=True)

    def _prune_automatic(self) -> None:
        """Retain seven recent automatic copies and one from four earlier ISO weeks.

        Manual and pre-migration copies are never pruned here. Retention is
        called only after a freshly created automatic ZIP was verified.
        """
        entries = self._automatic_files()
        kept_weeks = set()
        retained = set()
        for stamp, path in entries[:7]:
            retained.add(path)
            kept_weeks.add((stamp.isocalendar().year, stamp.isocalendar().week))
        other_weeks = 0
        for stamp, path in entries[7:]:
            week = (stamp.isocalendar().year, stamp.isocalendar().week)
            if week not in kept_weeks and other_weeks < 4:
                kept_weeks.add(week)
                retained.add(path)
                other_weeks += 1
        for _, path in entries:
            if path not in retained:
                path.unlink(missing_ok=True)

    def ensure_automatic(self, *, now: datetime | None = None) -> dict:
        """Take a daily backup while the application is running.

        The timestamp in verified local filenames survives app restarts. A
        backup that fails never advances the effective schedule.
        """
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Use timezone-aware UTC time for the backup schedule.")
        with self._creation_lock:
            copies = self._automatic_files()
            if any(
                timedelta(0) <= current - stamp < timedelta(hours=24)
                for stamp, _ in copies
            ):
                return {"state": "not_due"}
            created = create_full_backup(self.store, reason="automatic")
            try:
                self._prune_automatic()
            except OSError:
                logging.getLogger("taverna").warning(
                    "Не удалось удалить старые автоматические копии; новый архив сохранён."
                )
            return {"state": "created", "name": created.name}

    async def automatic_loop(self) -> None:
        # No background daemon outside Taverna: this task exists only while
        # the local application is running.
        while True:
            try:
                await asyncio.to_thread(self.ensure_automatic)
            except Exception as exc:
                logging.getLogger("taverna").warning(
                    "Автоматическое резервирование завершилось ошибкой: %s",
                    type(exc).__name__,
                )
            await asyncio.sleep(15 * 60)

    def verify(self, name: str):
        manifest = verify_full_backup(self._file(name))
        return {
            "name": name,
            "verified": True,
            "format": manifest["format"],
            "generation": manifest["generation"],
            "files": len(manifest["files"]),
        }

    def router(self):
        router = APIRouter(prefix="/api/v1/backups")

        @router.get("")
        def backups():
            return self.list()

        @router.post("")
        def create(body: BackupRequest):
            return self.create(body.reason)

        @router.post("/{name}/verify")
        def verify(name: str):
            return self.verify(name)

        @router.get("/{name}/download")
        def download(name: str):
            return FileResponse(self._file(name), filename=name, media_type="application/zip")

        return router

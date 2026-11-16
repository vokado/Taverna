"""Daily in-process backup scheduler.

Runs only while Taverna is open; never starts a separate OS service.
It uses the same verified full-backup path as the manual owner UI.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .backup_api import FILE_NAME, BackupService

LOG = logging.getLogger("taverna.backups")


class AutomaticBackups:
    def __init__(self, service: BackupService, *, interval: timedelta = timedelta(days=1), poll_seconds=3600):
        if interval.total_seconds() <= 0 or poll_seconds <= 0:
            raise ValueError("Backup schedule must be positive")
        self.service = service
        self.interval = interval
        self.poll_seconds = poll_seconds
        self.last_error_code: str | None = None
        self.last_checked_at: str | None = None
        self.last_created: str | None = None

    def _recent_automatic(self, now: datetime) -> bool:
        """Only a real regular file with a canonical UTC name satisfies a daily run."""
        for path in self.service.directory.glob("taverna-automatic-*.zip"):
            if path.is_symlink() or not path.is_file() or not FILE_NAME.fullmatch(path.name):
                continue
            try:
                stamp = path.name.split("-")[2]
                created = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            age = now - created
            if timedelta(0) <= age < self.interval:
                return True
        return False

    def ensure_due(self, now: datetime | None = None) -> str | None:
        """One due-check and one complete verified backup, under the manual-backup lock."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Backup clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        with self.service.lock:
            if self._recent_automatic(now):
                return None
            created = self.service.create("automatic")
            self.last_created = created["name"]
            return created["name"]

    async def run(self) -> None:
        """An error is visible in status/logs and retried on the next periodic check."""
        while True:
            self.last_checked_at = datetime.now(timezone.utc).isoformat()
            try:
                await asyncio.to_thread(self.ensure_due)
                self.last_error_code = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log user content, paths, archive names, or exception details.
                self.last_error_code = getattr(exc, "code", "backup_failed")
                LOG.warning("Автоматическое резервирование: %s", self.last_error_code)
            await asyncio.sleep(self.poll_seconds)

    def status(self) -> dict:
        return {
            "enabled": True,
            "interval_hours": self.interval.total_seconds() / 3600,
            "last_checked_at": self.last_checked_at,
            "last_created": self.last_created,
            "last_error_code": self.last_error_code,
            "only_while_running": True,
        }

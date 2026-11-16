"""Local diagnostic ZIP; excludes stories, media, prompts and private records by default."""

from __future__ import annotations

import io
import json
import platform
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter
from fastapi.responses import Response

from . import __version__

# Best-effort redaction is NOT a guarantee that an opted-in log is anonymous.
_IP4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_SECRET = re.compile(
    r"(?i)\b(authorization|api[_-]?key|password|token|secret|cookie)\s*[:=]\s*\S+"
)
_WIN_HOME = re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")
_POSIX_HOME = re.compile(r"/home/[^/\s]+")
_MAX_LOG_FILES = 5
_MAX_LOG_BYTES = 128 * 1024


def redact(text: str) -> str:
    text = _SECRET.sub(lambda m: m.group(1) + "=[СКРЫТО]", text)
    text = _WIN_HOME.sub("[ПАПКА_ПОЛЬЗОВАТЕЛЯ]", text)
    text = _POSIX_HOME.sub("[ПАПКА_ПОЛЬЗОВАТЕЛЯ]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    return _IP4.sub("[IP]", text)


class DiagnosticService:
    def __init__(self, store):
        self.store = store

    def archive(self, include_logs: bool = False) -> bytes:
        integrity = self.store.integrity()
        code_counts = Counter(str(e.get("code", "unknown")) for e in integrity["errors"])
        report = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": __version__,
            "os_family": platform.system(),
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "data": {
                "integrity_ok": bool(integrity["ok"]),
                "document_count": int(integrity["documents"]),
                "generation": int(integrity["generation"]),
                "error_codes": dict(sorted(code_counts.items())),
            },
            "private_content_included": False,
            "logs_included": bool(include_logs),
        }
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "report.json",
                json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            )
            archive.writestr(
                "README.txt",
                (
                    "Таверна: локальная диагностика.\n"
                    "Истории, промпты, персонажи, материалы, конфигурации и ключи не включены.\n"
                    "Архив никуда не передаётся автоматически.\n"
                    "При добавлении журналов выполнена только частичная маскировка.\n"
                    "Перед передачей третьим лицам проверьте содержимое архива.\n"
                ).encode("utf-8"),
            )
            if include_logs:
                # Explicit opt-in only. Keep filenames anonymous and never follow links.
                folder: Path = self.store.root / "logs"
                files = sorted(folder.glob("*.log"), key=lambda p: p.name)[:_MAX_LOG_FILES]
                for index, path in enumerate(files):
                    if path.is_symlink() or not path.is_file():
                        continue
                    with path.open("rb") as source:
                        content = source.read(_MAX_LOG_BYTES)
                    archive.writestr(
                        f"logs/journal-{index + 1}.txt",
                        redact(content.decode("utf-8", errors="replace")).encode("utf-8"),
                    )
        return output.getvalue()

    def router(self):
        api = APIRouter(prefix="/api/v1/diagnostics")

        @api.get("/archive")
        def download(include_logs: bool = False):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            name = f"Taverna-Diagnostics-{stamp}.zip"
            return Response(
                content=self.archive(include_logs),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{name}"',
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        return api

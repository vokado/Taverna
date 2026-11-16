"""Owner-only HTTP entrypoints for full local backup creation and verification."""

from __future__ import annotations

import re
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
        path = create_full_backup(self.store, reason=reason)
        return {"name": path.name, "bytes": path.stat().st_size}

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

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


@dataclass(frozen=True)
class Config:
    root: Path
    port: int = 8765
    max_body: int = 16 * 1024 * 1024

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def static(self) -> Path:
        return self.root / "app/web/dist"

    def prepare(self) -> None:
        if not self.root.is_dir():
            raise AppError("root_unavailable", "Каталог программы недоступен.")
        for name in ("data/config", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        probe = self.root / "data" / f".write-{uuid4().hex}"
        try:
            probe.write_bytes(b"test")
        finally:
            probe.unlink(missing_ok=True)


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


class SafeLogFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        text = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[СКРЫТО]", text)
        return re.sub(r"(?i)((?:token|secret|password|authorization)\s*[=:]\s*)[^\s,;]+", r"\1[СКРЫТО]", text)


def setup_logging(root: Path, diagnostic: bool = False):
    root.joinpath("logs").mkdir(exist_ok=True)
    handler = logging.FileHandler(root / "logs/taverna.log", encoding="utf-8")
    handler.setFormatter(SafeLogFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("taverna")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if diagnostic else logging.INFO)
    return logger


def atomic_runtime_info(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)

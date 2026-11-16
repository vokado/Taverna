"""Read-only package inspection: no imports, plugin start, shell, pip or network."""

from __future__ import annotations

import io
import stat
import unicodedata
import zipfile

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import __version__
from .cards import parse_json
from .core import AppError
from .plugin_models import PluginManifest
from .plugin_worker import HostError, binary_budget
from .storage import digest, portable_parts

MAX_PACKAGE = 12 * 1024 * 1024
MAX_UNPACKED = 32 * 1024 * 1024


class PluginPackages:
    def __init__(self, root):
        self.root = root

    def inspect(self, raw):
        if not raw or len(raw) > MAX_PACKAGE:
            raise AppError("plugin_package_limit", "Пакет пуст или больше12МиБ.", 413)
        files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 128 or sum(e.file_size for e in entries) > MAX_UNPACKED:
                    raise ValueError("archive limit")
                seen = set()
                for entry in entries:
                    if entry.orig_filename != entry.filename:
                        raise ValueError("ZIP filename was normalized or truncated")
                    name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                    if name != unicodedata.normalize("NFC", name):
                        raise ValueError("noncanonical filename")
                    portable_parts(name)
                    canonical = name.casefold()
                    if canonical in seen:
                        raise ValueError("duplicate Windows filename")
                    seen.add(canonical)
                    file_type = stat.S_IFMT(entry.external_attr >> 16)
                    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                        raise ValueError("link/encrypted")
                    if entry.file_size > 8 * 1024 * 1024 or entry.file_size > max(
                        1024, entry.compress_size * 200
                    ):
                        raise ValueError("archive ratio")
                    if entry.is_dir():
                        continue
                    files[name] = archive.read(entry)
            file_names = {name.casefold() for name in files}
            for name in files:
                parts = portable_parts(name)
                if any("/".join(parts[:index]).casefold() in file_names for index in range(1, len(parts))):
                    raise ValueError("file directory collision")
            manifest = PluginManifest.model_validate(parse_json(files["manifest.json"]))
            portable_parts(manifest.entry)
            if manifest.entry == "manifest.json" or manifest.entry not in files:
                raise ValueError("entry missing")
            entry = files[manifest.entry]
            if manifest.runtime == "wasm":
                binary_budget(entry)
            elif manifest.runtime == "trusted_python":
                if len(entry) > 256 * 1024:
                    raise ValueError("entry too large")
                # compile parses Python without executing top-level statements.
                compile(entry.decode("utf-8"), "<plugin-import-preview>", "exec")
            else:
                if len(entry) > 256 * 1024:
                    raise ValueError("declarative entry too large")
                plan = parse_json(entry)
                if (
                    set(plan) - {"format", "actions", "message"}
                    or plan.get("format") != 1
                    or not isinstance(plan.get("actions"), list)
                    or len(plan["actions"]) > 32
                    or not isinstance(plan.get("message", ""), str)
                ):
                    raise ValueError("declarative format")
            compatible = SpecifierSet(manifest.application_versions).contains(
                Version(__version__), prereleases=True
            )
            return {
                "manifest": manifest.model_dump(mode="json"),
                "sha256": digest(raw),
                "files": {name: digest(content) for name, content in files.items()},
                "unpacked_bytes": sum(map(len, files.values())),
                "application_compatible": compatible,
                "abi_verified": False,
                "warning_ru": "Python имеет полный доступ к файлам, сети, командам и данным пользователя; отдельный процесс не ограничивает эти права."
                if manifest.runtime == "trusted_python"
                else None,
            }, files
        except AppError:
            raise
        except HostError as exc:
            raise AppError(exc.code, exc.message, 422) from exc
        except (
            ValueError,
            KeyError,
            TypeError,
            UnicodeError,
            SyntaxError,
            RecursionError,
            zipfile.BadZipFile,
            NotImplementedError,
            RuntimeError,
        ) as exc:
            raise AppError(
                "plugin_package_invalid", "Манифест/entry/структура ZIP недопустимы; код не исполнялся.", 422
            ) from exc

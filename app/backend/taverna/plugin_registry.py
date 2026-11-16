from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
from uuid import UUID

import portalocker
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import __version__
from .core import AppError
from .plugin_models import PluginRecord
from .plugin_packages import PluginPackages
from .storage import Mutation, atomic_write, digest, document, is_redirect, safe_path

CATALOG = "config/plugin-catalog.json"


class PluginRegistry:
    def __init__(self, store):
        self.store, self.packages = store, PluginPackages(store.root)
        store.register("plugin_registration", 1, PluginRecord.model_json_schema())

    @staticmethod
    def key(pid):
        return "automations/plugins/" + str(UUID(str(pid))) + ".json"

    def get(self, pid, view=None):
        value = (view.get if view else self.store.get)(self.key(pid))
        if not value or value["kind"] != "plugin_registration":
            raise AppError("plugin_missing", "Расширение не найдено.", 404)
        try:
            value["payload"] = PluginRecord.model_validate(value["payload"]).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "plugin_record_invalid",
                "Запись расширения повреждена или имеет неподдерживаемый формат.",
                409,
            ) from exc
        if value["id"] != str(UUID(str(pid))) or value["payload"]["manifest"]["id"] != value["id"]:
            raise AppError("plugin_record_invalid", "UUID расширения и манифеста не совпадают.", 409)
        return value

    def snapshot(self):
        records, errors = {}, []
        with self.store.view() as view:
            catalog = view.get(CATALOG) or document("plugin_catalog", {"ids": []})
            if len(catalog["payload"]["ids"]) > 128 or len(set(catalog["payload"]["ids"])) != len(
                catalog["payload"]["ids"]
            ):
                raise AppError("plugin_catalog_invalid", "Каталог расширений повреждён.", 409)
            for pid in catalog["payload"]["ids"]:
                try:
                    records[pid] = self.get(pid, view)
                except (AppError, ValueError):
                    errors.append(pid)
            revision = catalog["revision"] if CATALOG in view.manifest["entries"] else 0
        return catalog, revision, records, errors

    def list(self):
        _, revision, records, errors = self.snapshot()
        return {"items": list(records.values()), "catalog_revision": revision, "errors": errors}

    @staticmethod
    def dependents(pid, records):
        affected = {pid}
        while True:
            add = {
                key
                for key, row in records.items()
                if any(dep["plugin_id"] in affected for dep in row["payload"]["manifest"]["dependencies"])
            }
            if add.issubset(affected):
                return sorted(affected - {pid})
            affected |= add

    def preview(self, raw):
        result, _ = self.packages.inspect(raw)
        _, revision, records, errors = self.snapshot()
        pid = result["manifest"]["id"]
        old = records.get(pid)
        return {
            **result,
            "catalog_revision": revision,
            "expected_revision": old["revision"] if old else 0,
            "affected_dependents": self.dependents(pid, records) if old else [],
            "catalog_errors": errors,
            "will_reset_grants": bool(old and old["payload"]["package_sha256"] != result["sha256"]),
        }

    def plan_catalog(self, catalog, revision, records):
        catalog["payload"]["ids"] = sorted(records)
        catalog["refs"] = [self.key(pid) for pid in sorted(records)]
        return Mutation(CATALOG, catalog, revision)

    @staticmethod
    def disable_changes(pids, records):
        changes = []
        for pid in pids:
            row = records[pid]
            if row["payload"]["enabled"]:
                row["payload"]["enabled"] = False
                row["payload"]["permission_epoch"] += 1
                changes.append(Mutation(PluginRegistry.key(pid), row, row["revision"]))
        return changes

    def verify(self, row):
        data = PluginRecord.model_validate(row["payload"]).model_dump(mode="json")
        files = {}
        for name, checksum in data["files"].items():
            path = safe_path(self.store.root, data["directory"] + "/" + name)
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise AppError(
                    "plugin_file_corrupt", "Файл расширения отсутствует или превышает допустимый размер.", 409
                )
            raw = path.read_bytes()
            if digest(raw) != checksum:
                raise AppError(
                    "plugin_file_corrupt", "Checksum расширения изменился; включение и запуск запрещены.", 409
                )
            files[name] = raw
        return files

    def install(self, raw, expected_revision, catalog_revision, approved_sha256, confirmed):
        if not confirmed:
            raise AppError(
                "confirmation_required", "Подтвердите установку просмотренного пакета без запуска кода.", 422
            )
        preview, files = self.packages.inspect(raw)
        if preview["sha256"] != approved_sha256:
            raise AppError("plugin_preview_conflict", "Checksum не совпадает с просмотренным пакетом.", 409)
        pid = preview["manifest"]["id"]
        lock = safe_path(self.store.root, "plugins/" + pid + ".lock")
        with portalocker.Lock(lock, timeout=10):
            catalog, revision, records, errors = self.snapshot()
            if errors:
                raise AppError(
                    "plugin_catalog_invalid", "Исправьте повреждённые записи каталога перед установкой.", 409
                )
            old = records.get(pid)
            if revision != catalog_revision or (old["revision"] if old else 0) != expected_revision:
                raise AppError(
                    "plugin_state_conflict", "Каталог изменился. Обновите предпросмотр установки.", 409
                )
            if old and old["payload"]["package_sha256"] == approved_sha256:
                self.verify(old)
                return old
            if not old and len(records) >= 128:
                raise AppError("plugin_catalog_limit", "Достигнут текущий предел128расширений.", 413)
            directory = "plugins/" + pid + "/" + preview["sha256"]
            for name, content in files.items():
                path = safe_path(self.store.root, directory + "/" + name)
                if path.exists():
                    if digest(path.read_bytes()) != digest(content):
                        raise AppError("plugin_file_corrupt", "Существующий immutable пакет повреждён.", 409)
                else:
                    atomic_write(path, content)
            value = copy.deepcopy(old) if old else document("plugin_registration", {}, entity_id=pid)
            value["payload"] = PluginRecord(
                manifest=preview["manifest"],
                package_sha256=preview["sha256"],
                directory=directory,
                files=preview["files"],
                permission_epoch=old["payload"]["permission_epoch"] + 1 if old else 0,
            ).model_dump(mode="json")
            changes = self.disable_changes(self.dependents(pid, records), records) if old else []
            records[pid] = value
            self.store.transaction(
                [
                    *changes,
                    Mutation(self.key(pid), value, expected_revision),
                    self.plan_catalog(catalog, revision, records),
                ]
            )
            return self.get(pid)

    def validate_dependencies(self, pid, records):
        visiting, done = set(), set()

        def visit(key):
            if key in visiting:
                raise AppError("plugin_dependency_cycle", "В зависимостях расширений обнаружен цикл.", 409)
            if key in done:
                return
            row = records.get(key)
            if not row:
                raise AppError(
                    "plugin_dependency_missing",
                    "Установите зависимости вручную; автоматической загрузки нет.",
                    409,
                )
            manifest = row["payload"]["manifest"]
            if not SpecifierSet(manifest["application_versions"]).contains(
                Version(__version__), prereleases=True
            ):
                raise AppError(
                    "plugin_incompatible", "Версия расширения несовместима с текущей Таверной.", 409
                )
            visiting.add(key)
            for dep in manifest["dependencies"]:
                child = records.get(dep["plugin_id"])
                if not child or not SpecifierSet(dep["version"]).contains(
                    Version(child["payload"]["manifest"]["version"]), prereleases=True
                ):
                    raise AppError(
                        "plugin_dependency_version", "Отсутствует подходящая версия зависимости.", 409
                    )
                visit(dep["plugin_id"])
                if not child["payload"]["enabled"]:
                    raise AppError("plugin_dependency_disabled", "Сначала явно включите зависимости.", 409)
            visiting.remove(key)
            done.add(key)

        visit(pid)

    def validate_grants(self, data):
        requests = {r["capability"]: r for r in data["manifest"]["requests"]}
        for grant in data["grants"]:
            requested = requests[grant["capability"]]["scopes"]
            if requested and "*" not in requested and not set(grant["scopes"]).issubset(requested):
                raise AppError(
                    "plugin_scope_not_requested", "Область разрешения не запрашивалась манифестом.", 403
                )
            if grant["capability"].startswith("filesystem."):
                for scope in grant["scopes"]:
                    path = Path(scope)
                    if scope.startswith(("\\\\", "//")) or not path.is_absolute():
                        raise AppError(
                            "plugin_scope_invalid", "Выберите абсолютный локальный каталог, без UNC.", 422
                        )
                    if os.name == "nt":
                        import ctypes

                        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
                            raise AppError(
                                "plugin_scope_invalid",
                                "Сетевые диски не поддерживаются файловым broker.",
                                422,
                            )
                    if ".." in path.parts:
                        raise AppError("plugin_scope_invalid", "Каталог не должен содержать переходы ..", 422)
                    for part in (path, *path.parents):
                        if is_redirect(part):
                            raise AppError(
                                "plugin_scope_invalid",
                                "Выберите настоящий каталог, без перенаправлений.",
                                422,
                            )
                    if not path.is_dir():
                        raise AppError(
                            "plugin_scope_invalid",
                            "Для файлового доступа выберите существующий абсолютный каталог.",
                            422,
                        )
                    path = path.resolve()
                    root = self.store.root.resolve()
                    if path.is_relative_to(root) or root.is_relative_to(path):
                        raise AppError(
                            "plugin_scope_forbidden",
                            "Прямой файловый доступ к программе/данным/плагинам запрещён; используйте broker API данных.",
                            403,
                        )
            elif grant["capability"] == "commands.exec":
                for scope in grant["scopes"]:
                    if not Path(scope).is_absolute() or not Path(scope).is_file():
                        raise AppError(
                            "plugin_scope_invalid",
                            "Выберите точный существующий executable, без shell/wildcard.",
                            422,
                        )

    def configure(
        self,
        pid,
        expected_revision,
        catalog_revision,
        enabled,
        grants,
        trusted_ack,
        approved_sha256,
        confirmed,
    ):
        if not confirmed:
            raise AppError(
                "confirmation_required", "Подтвердите изменение состояния и отдельных разрешений.", 422
            )
        catalog, revision, records, errors = self.snapshot()
        if errors:
            raise AppError("plugin_catalog_invalid", "Каталог содержит повреждённые записи.", 409)
        row = records.get(pid)
        if not row:
            raise AppError("plugin_missing", "Расширение не найдено.", 404)
        if (
            revision != catalog_revision
            or row["revision"] != expected_revision
            or approved_sha256 != row["payload"]["package_sha256"]
        ):
            raise AppError(
                "plugin_state_conflict", "Расширение/каталог/код изменились; перечитайте состояние.", 409
            )
        old = row["payload"]
        try:
            data = PluginRecord.model_validate(
                {
                    **old,
                    "enabled": enabled,
                    "grants": grants,
                    "trusted_full_access_ack": trusted_ack,
                    "permission_epoch": old["permission_epoch"] + 1,
                }
            ).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "plugin_permissions_invalid",
                "Разрешения/доверие не соответствуют манифесту и режиму исполнения.",
                422,
            ) from exc
        self.validate_grants(data)
        row["payload"] = data
        if enabled:
            self.verify(row)
            self.validate_dependencies(pid, records)
        changes = [] if enabled else self.disable_changes(self.dependents(pid, records), records)
        self.store.transaction(
            [
                *changes,
                Mutation(self.key(pid), row, expected_revision),
                self.plan_catalog(catalog, revision, records),
            ]
        )
        return self.get(pid)

    def remove(self, pid, expected_revision, catalog_revision, approved_sha256, confirmed):
        if not confirmed:
            raise AppError(
                "confirmation_required", "Подтвердите удаление расширения и отключение зависимых.", 422
            )
        lock = safe_path(self.store.root, "plugins/" + str(UUID(pid)) + ".lock")
        with portalocker.Lock(lock, timeout=10):
            catalog, revision, records, errors = self.snapshot()
            if errors:
                raise AppError("plugin_catalog_invalid", "Каталог содержит повреждённые записи.", 409)
            row = records.get(pid)
            if not row:
                raise AppError("plugin_missing", "Расширение не найдено.", 404)
            if (
                row["revision"] != expected_revision
                or revision != catalog_revision
                or row["payload"]["package_sha256"] != approved_sha256
            ):
                raise AppError("plugin_state_conflict", "Удаление требует актуального предпросмотра.", 409)
            affected = self.dependents(pid, records)
            changes = self.disable_changes(affected, records)
            del records[pid]
            self.store.transaction(
                [
                    *changes,
                    Mutation(self.key(pid), None, expected_revision),
                    self.plan_catalog(catalog, revision, records),
                ]
            )
            removed = False
            try:
                path = safe_path(self.store.root, "plugins/" + pid)
                if path.exists():
                    # Reject links/junctions before recursive removal. Broker scopes cannot
                    # write into the application; same-user OS attackers remain out of scope.
                    for child in path.rglob("*"):
                        safe_path(self.store.root, str(child.relative_to(self.store.root)).replace("\\", "/"))
                    shutil.rmtree(path)
                removed = True
            except (AppError, OSError):
                pass
            return {
                "deleted": True,
                "files_removed": removed,
                "disabled_dependents": affected,
                "directory": "plugins/" + pid if not removed else None,
            }

    def require_current(self, pid, revision, epoch, checksum, view=None, *, catalog_revision=None):
        if catalog_revision is not None:
            if view is None:
                with self.store.view() as captured:
                    return self.require_current(
                        pid, revision, epoch, checksum, captured, catalog_revision=catalog_revision
                    )
            catalog = view.get(CATALOG)
            if not catalog or catalog["revision"] != catalog_revision:
                raise AppError(
                    "plugin_catalog_conflict",
                    "Каталог расширений изменился. Перечитайте пакет и права перед новым запуском.",
                    409,
                )
        row = self.get(pid, view)
        data = row["payload"]
        if (
            not data["enabled"]
            or row["revision"] != revision
            or data["permission_epoch"] != epoch
            or data["package_sha256"] != checksum
        ):
            raise AppError(
                "plugin_permission_revoked", "Расширение выключено, изменено или разрешение отозвано.", 403
            )
        return row

    @staticmethod
    def allows(row, capability, target):
        return row["payload"]["enabled"] and any(
            g["capability"] == capability and ("*" in g["scopes"] or target in g["scopes"])
            for g in row["payload"]["grants"]
        )

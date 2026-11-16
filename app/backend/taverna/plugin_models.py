from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import Field, model_validator

from .world_models import Strict

Capability = Literal[
    "own.read",
    "own.write",
    "notes.read",
    "notes.write",
    "books.read",
    "books.write",
    "filesystem.read",
    "filesystem.write",
    "network.request",
    "tools.run",
    "commands.exec",
]


class PluginField(Strict):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "number", "checkbox", "select"] = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if not re.fullmatch(r"[\w-]{1,64}", self.key) or any(len(v) > 1000 for v in self.options):
            raise ValueError("Некорректное поле ввода")
        if (self.type == "select") != bool(self.options):
            raise ValueError("Варианты задаются только для select")
        return self


class CapabilityRequest(Strict):
    capability: Capability
    reason: str = Field(min_length=1, max_length=2000)
    scopes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def bounded(self):
        if any(not v or len(v) > 4000 or any(ord(c) < 32 for c in v) for v in self.scopes):
            raise ValueError("Некорректная область доступа")
        return self


class PluginDependency(Strict):
    plugin_id: UUID
    version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid(self):
        SpecifierSet(self.version)
        return self


class PluginManifest(Strict):
    format: Literal[1] = 1
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    api_version: Literal[1] = 1
    application_versions: str = Field(default=">=0.1.0.dev1,<2", max_length=200)
    description: str = Field(default="", max_length=8000)
    license: str = Field(default="", max_length=300)
    runtime: Literal["declarative", "wasm", "trusted_python"]
    entry: str = Field(min_length=1, max_length=250)
    fields: list[PluginField] = Field(default_factory=list, max_length=50)
    dependencies: list[PluginDependency] = Field(default_factory=list, max_length=50)
    requests: list[CapabilityRequest] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def consistent(self):
        Version(self.version)
        SpecifierSet(self.application_versions)
        if not self.name.strip():
            raise ValueError("Название не может быть пустым")
        if not self.entry.endswith(
            {"declarative": ".json", "wasm": ".wasm", "trusted_python": ".py"}[self.runtime]
        ):
            raise ValueError("Тип entry не соответствует runtime")
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError("Повторный ключ поля")
        if len({dep.plugin_id for dep in self.dependencies}) != len(self.dependencies) or self.id in {
            dep.plugin_id for dep in self.dependencies
        }:
            raise ValueError("Повторная/собственная зависимость")
        if len({r.capability for r in self.requests}) != len(self.requests):
            raise ValueError("Повторное разрешение")
        return self


class PluginGrant(Strict):
    capability: Capability
    scopes: list[str] = Field(min_length=1, max_length=100)
    full_access_warning_ack: bool = False

    @model_validator(mode="after")
    def scope(self):
        if any(not v or len(v) > 4000 or any(ord(c) < 32 for c in v) for v in self.scopes):
            raise ValueError("Некорректная область разрешения")
        if self.capability == "commands.exec" and (not self.full_access_warning_ack or "*" in self.scopes):
            raise ValueError("Команды требуют точный executable и подтверждение полного доступа команды")
        return self


class PluginRecord(Strict):
    format: Literal[1] = 1
    manifest: PluginManifest
    package_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    directory: str
    files: dict[str, str] = Field(max_length=128)
    enabled: bool = False
    grants: list[PluginGrant] = Field(default_factory=list, max_length=32)
    permission_epoch: int = Field(default=0, ge=0)
    trusted_full_access_ack: bool = False

    @model_validator(mode="after")
    def registry_invariants(self):
        if self.directory != f"plugins/{self.manifest.id}/{self.package_sha256}":
            raise ValueError("Каталог расширения должен соответствовать UUID и checksum")
        if (
            self.manifest.entry not in self.files
            or "manifest.json" not in self.files
            or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in self.files.values())
        ):
            raise ValueError("Отсутствуют entry/manifest/checksum файлов")
        caps = [grant.capability for grant in self.grants]
        if len(caps) != len(set(caps)) or not set(caps).issubset(
            {request.capability for request in self.manifest.requests}
        ):
            raise ValueError("Разрешены только отдельные запрошенные capabilities без повторов")
        if self.trusted_full_access_ack and self.manifest.runtime != "trusted_python":
            raise ValueError("Полное доверие относится только к trusted Python")
        if self.enabled and self.manifest.runtime == "trusted_python" and not self.trusted_full_access_ack:
            raise ValueError("Включение Python требует подтверждения полного доступа")
        return self

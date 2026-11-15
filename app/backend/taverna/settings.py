from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .core import AppError
from .storage import JsonStore, Mutation, document

SETTINGS_SCHEMA = {
    "type": "object",
    "required": ["level", "theme", "economy", "font", "font_size", "colors", "expert"],
    "properties": {
        "level": {"enum": ["simple", "advanced", "expert"]},
        "theme": {"enum": ["night", "warm", "light", "custom"]},
        "economy": {"type": "boolean"},
        "font": {"type": "string", "minLength": 1, "maxLength": 100},
        "font_size": {"type": "integer", "minimum": 12, "maximum": 28},
        "colors": {
            "type": "object",
            "required": ["background", "surface", "text", "accent"],
            "additionalProperties": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        },
        "expert": {"type": "object"},
        "active_workspace": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}
WORKSPACE_SCHEMA = {
    "type": "object",
    "required": ["name", "immersive", "panels"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "immersive": {"type": "boolean"},
        "panels": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["id", "width", "collapsed"],
                "properties": {
                    "id": {"enum": ["models", "history", "storage"]},
                    "width": {"type": "integer", "minimum": 300, "maximum": 1200},
                    "collapsed": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}
DEFAULT_SETTINGS = {
    "level": "simple",
    "theme": "night",
    "economy": False,
    "font": "Segoe UI",
    "font_size": 16,
    "colors": {"background": "#171a20", "surface": "#22262e", "text": "#e9e5df", "accent": "#d6a25f"},
    "expert": {},
    "active_workspace": None,
}


def merge(current: dict, patch: dict) -> dict:
    result = dict(current)
    for key, value in patch.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else value
        )
    return result


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    patch: dict


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    workspace: dict


class SettingsService:
    def __init__(self, store: JsonStore):
        self.store = store
        store.register("user_settings", 1, SETTINGS_SCHEMA)
        store.register("workspace", 1, WORKSPACE_SCHEMA)

    def get(self):
        value = self.store.get("config/settings.json")
        return {
            "revision": value["revision"] if value else 0,
            "settings": value["payload"] if value else DEFAULT_SETTINGS,
        }

    def patch(self, body: SettingsPatch):
        old = self.store.get("config/settings.json")
        value = old or document("user_settings", DEFAULT_SETTINGS)
        value["payload"] = merge(value["payload"], body.patch)
        if "expert" in body.patch:
            value["payload"]["expert"] = body.patch["expert"]
        self.store.validate(value)
        workspace = value["payload"].get("active_workspace")
        if workspace:
            try:
                UUID(workspace)
            except ValueError as exc:
                raise AppError("invalid_workspace", "Некорректное рабочее пространство.") from exc
            if not self.store.get(f"config/workspaces/{workspace}.json"):
                raise AppError("workspace_missing", "Рабочее пространство не найдено.", 404)
        self.store.transaction([Mutation("config/settings.json", value, body.expected_revision)])
        return self.get()

    def save_workspace(self, body: WorkspaceRequest, workspace_id=None):
        if workspace_id:
            try:
                UUID(workspace_id)
            except ValueError as exc:
                raise AppError("invalid_workspace", "Некорректное рабочее пространство.") from exc
        key = f"config/workspaces/{workspace_id}.json" if workspace_id else None
        old = self.store.get(key) if key else None
        if workspace_id and not old:
            raise AppError("workspace_missing", "Рабочее пространство не найдено.", 404)
        value = old or document("workspace", body.workspace)
        value["payload"] = body.workspace
        self.store.validate(value)
        ids = [
            panel["id"]
            for panel in body.workspace.get("panels", [])
            if isinstance(panel, dict) and "id" in panel
        ]
        if len(set(ids)) != len(ids):
            raise AppError("duplicate_panel", "Панели рабочего пространства не должны повторяться.")
        self.store.transaction(
            [Mutation(key or f"config/workspaces/{value['id']}.json", value, body.expected_revision)]
        )
        return self.store.get(f"config/workspaces/{value['id']}.json")

    def router(self):
        router = APIRouter(prefix="/api/v1")

        @router.get("/settings")
        def settings():
            return self.get()

        @router.patch("/settings")
        def update_settings(body: SettingsPatch):
            return self.patch(body)

        @router.get("/workspaces")
        def workspaces():
            rows, errors = self.store.scan("config/workspaces/", tolerate_corrupt=True)
            return {"workspaces": [row["value"] for row in rows], "errors": errors}

        @router.post("/workspaces")
        def create_workspace(body: WorkspaceRequest):
            return self.save_workspace(body)

        @router.patch("/workspaces/{workspace_id}")
        def update_workspace(workspace_id: str, body: WorkspaceRequest):
            return self.save_workspace(body, workspace_id)

        @router.get("/home/summary")
        def home():
            characters, ce = self.store.scan("characters/", tolerate_corrupt=True)
            stories, se = self.store.scan("chats/", tolerate_corrupt=True)
            scenarios, we = self.store.scan("scenarios/", tolerate_corrupt=True)
            return {
                "character_count": sum(row["value"]["kind"] == "character" for row in characters),
                "recent_stories": sorted(
                    [row["value"] for row in stories if row["value"]["kind"] == "story"],
                    key=lambda value: value["updated_at"],
                    reverse=True,
                )[:10],
                "scenario_count": sum(row["value"]["kind"] == "scenario_template" for row in scenarios),
                "errors": ce + se + we,
            }

        return router

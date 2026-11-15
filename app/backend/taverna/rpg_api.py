from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .rpg_models import RpgAction, RpgSettings
from .world_models import Strict


class ConfigureRpg(Strict):
    branch_id: UUID
    expected_revision: int = Field(ge=1)
    settings: RpgSettings
    reason: str = Field(min_length=1, max_length=2000)
    seed: str | None = Field(default=None, min_length=1, max_length=128)


def router(service):
    api = APIRouter(prefix="/api/v1/stories/{story_id}/rpg")

    @api.get("")
    def read(story_id: UUID, branch_id: UUID):
        return service.read(str(story_id), str(branch_id))

    @api.put("/settings")
    def configure(story_id: UUID, body: ConfigureRpg):
        return service.configure(
            str(story_id), str(body.branch_id), body.expected_revision, body.settings, body.reason, body.seed
        )

    @api.post("/actions")
    def apply(story_id: UUID, body: RpgAction):
        return service.apply(str(story_id), body)

    return api

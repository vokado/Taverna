"""Export actual runtime API/schema contracts without touching the user's data root."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from taverna.characters import CHARACTER_SCHEMA
from taverna.chat_schemas import BRANCH_SCHEMA, MESSAGE_SCHEMA, STATE_SCHEMA, STORY_SCHEMA
from taverna.core import Config
from taverna.director_models import (
    DirectorPolicy,
    DirectorProposal,
    DirectorResponse,
    DirectorState,
    NPCState,
)
from taverna.image_models import ImageLease, ImageRequest, ImageSettings
from taverna.knowledge import MemoryRecord
from taverna.main import create_app
from taverna.media_models import Asset, AssetMetadata, Composition, Layer, MediaState, Variant
from taverna.memory_ai import MemorySettings
from taverna.profiles import ProfileSettings
from taverna.prompts import CONTEXT_SCHEMA
from taverna.rpg_models import ActorSheet, DiceRequest, RandomTable, RpgAction, RpgSettings, RpgState
from taverna.settings import SETTINGS_SCHEMA, WORKSPACE_SCHEMA
from taverna.storage import BASE_SCHEMA
from taverna.world_models import Scenario, World

root = Path(__file__).resolve().parents[1]
schemas = root / "schemas"
schemas.mkdir(exist_ok=True)
with TemporaryDirectory() as temporary:
    contracts = {
        "openapi.json": create_app(Config(Path(temporary))).openapi(),
        "document-v1.json": BASE_SCHEMA,
        "profile-settings-v1.json": ProfileSettings.model_json_schema(),
        "generation-context-v1.json": CONTEXT_SCHEMA,
        "memory-record-v1.json": MemoryRecord.model_json_schema(),
        "image-settings-v1.json": ImageSettings.model_json_schema(),
        "image-request-v1.json": ImageRequest.model_json_schema(),
        "image-lease-v1.json": ImageLease.model_json_schema(),
        "media-asset-v1.json": Asset.model_json_schema(),
        "media-metadata-v1.json": AssetMetadata.model_json_schema(),
        "media-state-v1.json": MediaState.model_json_schema(),
        "media-composition-v1.json": Composition.model_json_schema(),
        "media-layer-v1.json": Layer.model_json_schema(),
        "media-variant-v1.json": Variant.model_json_schema(),
        "rpg-state-v1.json": RpgState.model_json_schema(),
        "rpg-settings-v1.json": RpgSettings.model_json_schema(),
        "rpg-actor-v1.json": ActorSheet.model_json_schema(),
        "rpg-dice-v1.json": DiceRequest.model_json_schema(),
        "rpg-action-v1.json": RpgAction.model_json_schema(),
        "rpg-random-table-v1.json": RandomTable.model_json_schema(),
        "npc-state-v1.json": NPCState.model_json_schema(),
        "director-policy-v1.json": DirectorPolicy.model_json_schema(),
        "director-state-v1.json": DirectorState.model_json_schema(),
        "director-response-v1.json": DirectorResponse.model_json_schema(),
        "director-proposal-v1.json": DirectorProposal.model_json_schema(),
        "world-template-v1.json": World.model_json_schema(),
        "scenario-template-v1.json": Scenario.model_json_schema(),
        "memory-policy-v1.json": MemorySettings.model_json_schema(),
        "character-payload-v1.json": CHARACTER_SCHEMA,
        "settings-v1.json": SETTINGS_SCHEMA,
        "workspace-v1.json": WORKSPACE_SCHEMA,
    }
    contracts.update(
        {
            "story-v1.json": STORY_SCHEMA,
            "branch-v1.json": BRANCH_SCHEMA,
            "branch-state-v1.json": STATE_SCHEMA,
            "message-v1.json": MESSAGE_SCHEMA,
        }
    )
    for name, value in contracts.items():
        (schemas / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Exported {len(contracts)} contracts")

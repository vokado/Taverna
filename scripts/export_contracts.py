"""Export actual runtime API/schema contracts without touching the user's data root."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from taverna.characters import CHARACTER_SCHEMA
from taverna.chat_schemas import BRANCH_SCHEMA, MESSAGE_SCHEMA, STATE_SCHEMA, STORY_SCHEMA
from taverna.core import Config
from taverna.main import create_app
from taverna.profiles import ProfileSettings
from taverna.prompts import CONTEXT_SCHEMA
from taverna.settings import SETTINGS_SCHEMA, WORKSPACE_SCHEMA
from taverna.storage import BASE_SCHEMA

root = Path(__file__).resolve().parents[1]
schemas = root / "schemas"
schemas.mkdir(exist_ok=True)
with TemporaryDirectory() as temporary:
    contracts = {
        "openapi.json": create_app(Config(Path(temporary))).openapi(),
        "document-v1.json": BASE_SCHEMA,
        "profile-settings-v1.json": ProfileSettings.model_json_schema(),
        "generation-context-v1.json": CONTEXT_SCHEMA,
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

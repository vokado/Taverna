"""Export actual runtime API/schema contracts without touching the user's data root."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from taverna.characters import CHARACTER_SCHEMA
from taverna.core import Config
from taverna.main import create_app
from taverna.settings import SETTINGS_SCHEMA, WORKSPACE_SCHEMA
from taverna.storage import BASE_SCHEMA

root = Path(__file__).resolve().parents[1]
schemas = root / "schemas"
schemas.mkdir(exist_ok=True)
with TemporaryDirectory() as temporary:
    contracts = {
        "openapi.json": create_app(Config(Path(temporary))).openapi(),
        "document-v1.json": BASE_SCHEMA,
        "character-payload-v1.json": CHARACTER_SCHEMA,
        "settings-v1.json": SETTINGS_SCHEMA,
        "workspace-v1.json": WORKSPACE_SCHEMA,
    }
    for name, value in contracts.items():
        (schemas / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Exported {len(contracts)} contracts")

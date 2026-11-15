from __future__ import annotations

import copy
from uuid import NAMESPACE_URL, uuid5

from .cards import parse_json
from .core import AppError
from .storage import digest, encoded
from .world_models import LoreEntry


def select_lore(world, actor, text):
    selected, excluded = [], []
    if world.get("format") != 1:
        return selected, excluded
    # Knowledge is tested before keys; a hidden entry cannot trigger another entry.
    accessible = []
    for raw in world["template"]["payload"]["lore"]:
        reason = None
        if not raw["enabled"]:
            reason = "disabled"
        elif not raw["public"] and actor not in raw["knower_ids"]:
            reason = "unknown_to_actor"
        if reason:
            excluded.append({"source": "lore", "source_id": raw["id"], "reason": reason})
        else:
            accessible.append(raw)
    haystack = text.casefold()
    for entry in sorted(accessible, key=lambda e: (e["priority"], e["id"])):
        primary = any(key.casefold() in haystack for key in entry["keys"])
        secondary = any(key.casefold() in haystack for key in entry["secondary_keys"])
        if entry["constant"] or primary and (not entry["selective"] or secondary):
            selected.append(("world-lore:" + entry["id"], entry["text"]))
        else:
            excluded.append({"source": "lore", "source_id": entry["id"], "reason": "trigger_not_matched"})
    return selected, excluded


def preview_lore(text, *, public=False):
    try:
        raw = parse_json(text.encode("utf-8"))
        if not isinstance(raw, dict) or "entries" not in raw:
            raise ValueError("entries")
        source = raw["entries"]
        if isinstance(source, dict):
            source = list(source.values())
        if not isinstance(source, list) or len(source) > 2000:
            raise ValueError("entries")
        entries, warnings = [], []
        fingerprint = digest(encoded(raw))
        for index, item in enumerate(source):
            if not isinstance(item, dict):
                raise ValueError("entry")
            unsupported = []
            if item.get("useRegex") or item.get("use_regex"):
                unsupported.append("регулярные выражения")
            if item.get("useProbability") and item.get("probability", 100) != 100:
                unsupported.append("вероятностное включение")
            if item.get("selectiveLogic", 0) != 0:
                unsupported.append("альтернативная логика вторичных ключей")
            if (
                item.get("vectorized")
                or item.get("matchPersonaDescription")
                or item.get("matchCharacterDescription")
            ):
                unsupported.append("дополнительные источники активации")
            keys = item.get("key", item.get("keys", []))
            secondary = item.get("keysecondary", item.get("secondary_keys", []))
            if (
                not isinstance(keys, list)
                or not isinstance(secondary, list)
                or any(not isinstance(k, str) for k in keys + secondary)
            ):
                raise ValueError("keys")
            name = item.get("comment") or item.get("name") or f"Запись {index + 1}"
            content = item.get("content")
            if not isinstance(name, str) or not isinstance(content, str):
                raise ValueError("content")
            entry = LoreEntry(
                id=uuid5(NAMESPACE_URL, f"taverna/lore/{fingerprint}/{index}"),
                name=name,
                text=content,
                keys=keys,
                secondary_keys=secondary,
                selective=bool(item.get("selective", False)),
                constant=bool(item.get("constant", False)),
                enabled=not bool(item.get("disable", False))
                and bool(item.get("enabled", True))
                and not unsupported,
                public=public,
                priority=max(0, min(1000, int(item.get("order", item.get("insertion_order", 50))))),
                extras={"sillytavern_original": copy.deepcopy(item)},
            ).model_dump(mode="json")
            entries.append(entry)
            if unsupported:
                warnings.append({"index": index, "message_ru": "Запись отключена: " + ", ".join(unsupported)})
            if any(
                key in item
                for key in (
                    "position",
                    "depth",
                    "excludeRecursion",
                    "preventRecursion",
                    "delayUntilRecursion",
                    "sticky",
                    "cooldown",
                    "delay",
                )
            ):
                warnings.append(
                    {
                        "index": index,
                        "message_ru": "Параметры размещения/рекурсии/задержек сохранены в оригинале; действует одношаговый триггер и бюджет блока lore.",
                    }
                )
        return {"entries": entries, "warnings": warnings, "original": raw, "sha256": fingerprint}
    except (ValueError, TypeError, OverflowError) as exc:
        raise AppError(
            "lore_import_invalid", "Лорбук не соответствует поддерживаемому JSON-формату SillyTavern.", 422
        ) from exc

from __future__ import annotations

import copy
import json
from uuid import NAMESPACE_URL, uuid5

from .core import AppError
from .director_models import NPCState
from .knowledge import text_hash
from .storage import digest, encoded

MODE_RULES = {
    "fixed": "Характер фиксирован: сохраняй основу карточки.",
    "flexible": "Характер гибкий: учитывай ситуативную адаптацию, сохраняя основу личности.",
    "developing": "Характер развивается: учитывай только подтверждённые изменения, не выдумывай биографию.",
}


def npc_value(story, state, actor):
    card = story["payload"]["character_snapshots"].get(actor)
    if not card:
        raise AppError("knowledge_scope", "NPC отсутствует в истории.", 422)
    raw = state["payload"]["character_states"].get(actor, {})
    try:
        if not isinstance(raw, dict):
            raise ValueError("Некорректный тип состояния NPC")
        if raw.get("format") not in (None, 1):
            raise AppError("unsupported_schema", "Неизвестная версия состояния NPC.", 409)
        if raw.get("format") == 1:
            return NPCState.model_validate(raw).model_dump(mode="json")
        return NPCState(
            mode=card["card"].get("taverna", {}).get("personality_mode", "fixed"),
            extras={"legacy": copy.deepcopy(raw)} if raw else {},
        ).model_dump(mode="json")
    except ValueError as exc:
        raise AppError(
            "invalid_npc_state", "Состояние NPC повреждено; исходные данные сохранены.", 503
        ) from exc


def actor_scene_id(story, state, actor):
    data = state["payload"]
    if actor in data["scene"]["participants"]:
        return data["scene"]["id"]
    policy = data.get("director", {}).get("policy", {})
    entity = data.get("world", {}).get("entities", {}).get(policy.get("actor_entities", {}).get(actor), {})
    return entity.get("location_id") or str(
        uuid5(NAMESPACE_URL, f"taverna/offscene/{story['id']}/{data['branch_id']}/{actor}")
    )


def derived_reason(value, actor, knowledge, card_sha256):
    sources, facts, card = (
        value.get("source_ids", []),
        value.get("source_fact_versions", {}),
        value.get("source_card_sha256"),
    )
    if not sources and not facts and not card:
        return "sources_missing"
    if card and card != card_sha256:
        return "source_card_changed"
    if (sources or facts) and knowledge is None:
        return "sources_unverified"
    for mid in sources:
        message = knowledge.messages.get(mid)
        if (
            not message
            or actor not in message.get("present_ids", [])
            and message.get("author_id") != actor
            or value.get("source_hashes", {}).get(mid) != text_hash(message["text"])
        ):
            return "source_message_hidden_or_changed"
    for fid, version in facts.items():
        record = knowledge.records.get(fid)
        if not record or record.get("version") != version or knowledge.reason(record, actor):
            return "source_fact_hidden_or_changed"
    return None


def npc_prompt(story, state, actor, knowledge=None, include_plans=True):
    npc = npc_value(story, state, actor)
    scene = actor_scene_id(story, state, actor)
    card_sha256 = digest(encoded(story["payload"]["character_snapshots"][actor]))
    current = {}
    if npc["traits"]:
        current["traits"] = npc["traits"]
    if state["payload"]["character_states"].get(actor, {}).get("format") == 1 and npc["emotions_enabled"]:
        current["emotion"] = npc["emotion"]
    if npc["mode"] == "flexible" and npc["adaptation_scene_id"] == scene and npc["adaptations"]:
        current["temporary_adaptations"] = npc["adaptations"]
    intentions = [
        intent["text"]
        for intent in npc["intentions"][-3:]
        if intent.get("scene_id") == scene
        and intent.get("status") == "proposed_action"
        and derived_reason(intent, actor, knowledge, card_sha256) is None
    ]
    if include_plans and intentions:
        current["possible_intentions"] = intentions
    arcs = [
        arc["description"]
        for arc in state["payload"].get("director", {}).get("arcs", {}).values()
        if actor in arc.get("knower_ids", [])
        and arc.get("status") == "open"
        and derived_reason(arc, actor, knowledge, card_sha256) is None
    ]
    if include_plans and arcs:
        current["known_open_arcs"] = arcs
    # No histories, raw model plans, extras, other actors or objective world state.
    return MODE_RULES[npc["mode"]] + (
        "\nПодтверждённые данные NPC: " + json.dumps(current, ensure_ascii=False) if current else ""
    )

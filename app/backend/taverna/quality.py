"""Observable heuristics, never a claim of semantic correctness."""

import json
import re
from difflib import SequenceMatcher


def user_utterance_marker(text):
    return bool(re.search(r"(?im)^\s*(?:пользователь|user|вы)\s*:", text))


def inspect_answer(text, previous, settings):
    flags = []
    if not text.strip():
        flags.append({"code": "empty", "message_ru": "Пустой ответ."})
    normal = " ".join(text.casefold().split())[:8192]
    if normal and any(
        SequenceMatcher(None, normal, " ".join(row.casefold().split())[:8192], autojunk=True).ratio()
        >= settings["repeat_threshold"]
        for row in previous[-3:]
    ):
        flags.append({"code": "repetition", "message_ru": "Ответ похож на предыдущую реплику персонажа."})
    if user_utterance_marker(text) or re.search(
        r"(?i)как (?:языковая|искусственный интеллект|ИИ[- ]?модель)", text
    ):
        flags.append(
            {
                "code": "role",
                "message_ru": "Возможный выход из роли или реплика за пользователя.",
                "user_impersonation": user_utterance_marker(text),
            }
        )
    if settings["format"] == "json":
        try:
            json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            flags.append({"code": "format", "message_ru": "Ответ не является корректным JSON."})
    elif settings["format"] == "dialogue" and not re.search(r'[«»"—]', text):
        flags.append({"code": "format", "message_ru": "Не обнаружено ожидаемого оформления диалога."})
    return flags

from __future__ import annotations

import base64
import binascii
import copy
import io
import json
import struct
import warnings
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from PIL import Image

from .core import AppError
from .storage import atomic_write, digest, encoded, safe_path

BASE_FIELDS = ("name", "description", "personality", "scenario", "first_mes", "mes_example")
V2_FIELDS = (
    *BASE_FIELDS,
    "creator_notes",
    "system_prompt",
    "post_history_instructions",
    "alternate_greetings",
    "character_book",
    "tags",
    "creator",
    "character_version",
    "extensions",
)
V3_EXTRA = (
    "assets",
    "nickname",
    "creator_notes_multilingual",
    "source",
    "group_only_greetings",
    "creation_date",
    "modification_date",
)
MAX_FILE = 12 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def parse_json(raw: bytes) -> dict:
    if len(raw) > MAX_FILE:
        raise AppError("import_too_large", "Слишком большой файл карточки.", 413)
    try:

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        result = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite")),
        )
        if not isinstance(result, dict):
            raise ValueError("object")
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AppError("invalid_import", "Файл не содержит корректный UTF-8 JSON-объект.", 422) from exc


def normalize_card(raw: dict) -> dict:
    spec = raw.get("spec")
    if spec is None:
        version = "v1"
        fields = copy.deepcopy(raw)
    elif spec in ("chara_card_v2", "chara_card_v3"):
        version = "v2" if spec == "chara_card_v2" else "v3"
        fields = copy.deepcopy(raw.get("data"))
    else:
        raise AppError("unsupported_card", "Версия формата карточки не поддерживается.", 422)
    if not isinstance(fields, dict):
        raise AppError("invalid_card", "В карточке отсутствует объект data.", 422)
    for name in BASE_FIELDS:
        if name not in fields or not isinstance(fields[name], str):
            raise AppError(
                "invalid_card", "В карточке отсутствует обязательное текстовое поле.", 422, field=name
            )
    if not fields["name"].strip():
        raise AppError("invalid_card", "Укажите имя персонажа.", 422)
    for name in ["alternate_greetings", "tags", "group_only_greetings"]:
        if name in fields and (
            not isinstance(fields[name], list) or any(not isinstance(x, str) for x in fields[name])
        ):
            raise AppError("invalid_card", "Список карточки должен содержать строки.", 422, field=name)
    if "extensions" in fields and not isinstance(fields["extensions"], dict):
        raise AppError("invalid_card", "extensions должен быть JSON-объектом.", 422)
    notes = []
    if spec and raw.get("spec_version") not in ("2.0", "3.0"):
        notes.append(
            "Версия спецификации отличается от проверенной. Неизвестные поля сохраняются, новые возможности могут не поддерживаться."
        )
    return {
        "source_format": version,
        "original": copy.deepcopy(raw),
        "fields": fields,
        "taverna": {
            "biography": "",
            "motivation": "",
            "habits": "",
            "fears": "",
            "speech_style": "",
            "sections": {},
            "personality_mode": "fixed",
            "presets": [],
        },
        "folder": "",
        "portrait_id": None,
        "compatibility_notes": notes,
    }


def new_card(name="Новый персонаж"):
    fields = {key: "" for key in V2_FIELDS if key != "character_book"}
    fields.update(name=name, alternate_greetings=[], tags=[], extensions={}, group_only_greetings=[])
    return normalize_card({"spec": "chara_card_v3", "spec_version": "3.0", "data": fields})


def export_card(payload: dict, target: str) -> tuple[dict, dict]:
    if target not in ("v1", "v2", "v3"):
        raise AppError("unsupported_card", "Целевой формат не поддерживается.")
    fields = copy.deepcopy(payload["fields"])
    original = copy.deepcopy(payload.get("original", {}))
    report = {
        "target": target,
        "unsupported_fields": [],
        "warnings": list(payload.get("compatibility_notes", [])),
    }
    # Only Taverna's own package is lossless for all app-specific data.
    report["unsupported_fields"].extend("taverna." + key for key in payload.get("taverna", {}))
    for key in ("folder", "portrait_id"):
        if payload.get(key):
            report["unsupported_fields"].append(key)
    for key in V3_EXTRA if target == "v2" else ():
        if key in fields:
            report["unsupported_fields"].append("data." + key)
            fields.pop(key)
    if target == "v1":
        report["unsupported_fields"] += ["data." + key for key in fields if key not in BASE_FIELDS]
        result = (
            fields if payload["source_format"] == "v1" else {key: fields.get(key, "") for key in BASE_FIELDS}
        )
    else:
        for key in V2_FIELDS:
            if key == "character_book":
                continue
            fields.setdefault(
                key, [] if key in ("alternate_greetings", "tags") else {} if key == "extensions" else ""
            )
        if target == "v3":
            fields.setdefault("group_only_greetings", [])
        result = {
            key: value for key, value in original.items() if key not in ("spec", "spec_version", "data")
        }
        if payload["source_format"] == "v1":
            result = {}
        result.update(
            spec="chara_card_" + target, spec_version="2.0" if target == "v2" else "3.0", data=fields
        )
    for key in original:
        if (
            key not in ("spec", "spec_version", "data")
            and payload["source_format"] != "v1"
            and target == "v1"
        ):
            report["unsupported_fields"].append(key)
    return result, report


def png_chunks(raw: bytes):
    if len(raw) > MAX_FILE or not raw.startswith(PNG_SIGNATURE):
        raise AppError("invalid_png", "Некорректный PNG или превышен размер.", 422)
    offset = 8
    chunks = []
    ended = False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise AppError("invalid_png", "Повреждённая структура PNG.", 422)
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        if length > MAX_FILE or offset + 12 + length > len(raw):
            raise AppError("invalid_png", "Повреждённый блок PNG.", 422)
        kind = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", raw[offset + 8 + length : offset + 12 + length])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise AppError("invalid_png", "Контрольная сумма PNG не совпадает.", 422)
        chunks.append((kind, data))
        offset += length + 12
        if kind == b"IEND":
            ended = True
            break
    if not ended or offset != len(raw):
        raise AppError("invalid_png", "Некорректное завершение PNG.", 422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                frames = getattr(image, "n_frames", 1)
                if (
                    image.format != "PNG"
                    or image.width * image.height > 16_000_000
                    or frames > 200
                    or image.width * image.height * frames > 64_000_000
                ):
                    raise ValueError("image limit")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise AppError(
            "invalid_png", "Изображение повреждено или превышает безопасные ограничения.", 422
        ) from exc
    return chunks


def import_png(raw: bytes) -> dict:
    chunks = png_chunks(raw)
    metadata = {}
    for kind, data in chunks:
        if kind == b"tEXt" and b"\0" in data:
            name, value = data.split(b"\0", 1)
            if name in (b"chara", b"ccv3"):
                if name in metadata:
                    raise AppError("invalid_png", "Повторные данные карточки в PNG.", 422)
                metadata[name] = value
    value = metadata.get(b"ccv3", metadata.get(b"chara"))
    if value is None:
        raise AppError("card_not_found", "В PNG не найдены данные карточки персонажа.", 422)
    try:
        return normalize_card(parse_json(base64.b64decode(value, validate=True)))
    except (ValueError, binascii.Error) as exc:
        raise AppError("invalid_png", "Данные карточки PNG повреждены.", 422) from exc


def export_png(payload: dict, target: str, original_png: bytes | None = None) -> tuple[bytes, dict]:
    if original_png is None:
        image = Image.new("RGBA", (256, 256), (49, 43, 39, 255))
        out = io.BytesIO()
        image.save(out, format="PNG")
        original_png = out.getvalue()
    chunks = png_chunks(original_png)
    card, report = export_card(payload, target)
    name = b"ccv3" if target == "v3" else b"chara"
    metadata = (b"tEXt", name + b"\0" + base64.b64encode(encoded(card)))
    result = bytearray(PNG_SIGNATURE)
    for kind, data in chunks:
        if kind == b"tEXt" and data.split(b"\0", 1)[0] in (b"chara", b"ccv3"):
            continue
        if kind == b"IEND":
            k, v = metadata
            result += struct.pack(">I", len(v)) + k + v + struct.pack(">I", zlib.crc32(k + v) & 0xFFFFFFFF)
        result += (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )
    return bytes(result), report


def read_package(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(raw) > MAX_FILE:
        raise AppError("import_too_large", "Пакет слишком большой.", 413)
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or len({x.filename for x in entries}) != len(entries):
                raise ValueError("entries")
            total = 0
            for entry in entries:
                safe_path(Path("/package"), entry.filename)
                total += entry.file_size
                if (
                    entry.is_dir()
                    or entry.flag_bits & 1
                    or entry.file_size > MAX_FILE
                    or total > 32 * 1024 * 1024
                ):
                    raise ValueError("limits")
            manifest = parse_json(archive.read("manifest.json"))
            if manifest.get("format") != "taverna-character-1":
                raise ValueError("format")
            checksums = manifest["files"]
            if set(archive.namelist()) != set(checksums) | {"manifest.json"}:
                raise ValueError("manifest")
            files = {name: archive.read(name) for name in checksums}
            if any(digest(data) != checksums[name] for name, data in files.items()):
                raise ValueError("checksum")
            return parse_json(files.pop("character.json")), files
    except (BadZipFile, OSError, KeyError, ValueError, TypeError) as exc:
        raise AppError("invalid_package", "Пакет повреждён или не поддерживается.", 422) from exc


def make_package(payload: dict, assets: dict[str, bytes]) -> bytes:
    files = {"character.json": encoded(payload), **assets}
    manifest = {
        "format": "taverna-character-1",
        "files": {name: digest(data) for name, data in files.items()},
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", encoded(manifest))
    return buffer.getvalue()


def save_portrait(root: Path, raw: bytes) -> dict:
    png_chunks(raw)
    checksum = digest(raw)
    path = f"media/portraits/{checksum}.png"
    atomic_write(safe_path(root, path), raw)
    return {"relative_path": path, "checksum": checksum, "type": "image/png"}

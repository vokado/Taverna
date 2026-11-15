"""Bounded attachment inspection. Documents are served as downloads, never active HTML."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

import pypdf
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

from .core import AppError

MAX_FILE = 12 * 1024 * 1024
MAX_TEXT = 512 * 1024


def inspect_document(raw, filename):
    if not raw or len(raw) > MAX_FILE:
        raise AppError("material_limit", "Документ пуст или больше12МиБ.", 413)
    if (
        len(filename) > 250
        or any(c in filename for c in "/\\:\x00")
        or filename in (".", "..")
        or any(ord(c) < 32 for c in filename)
    ):
        raise AppError("invalid_path", "Имя файла не должно содержать путь.", 422)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if extension in ("txt", "md", "json"):
            text = raw.decode("utf-8-sig")
            if len(text) > MAX_TEXT or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                raise ValueError("text limit/control")
            if extension == "json":
                json.loads(
                    text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON"))
                )
            return {
                "extension": extension,
                "mime": {"txt": "text/plain", "md": "text/markdown", "json": "application/json"}[extension],
                "text": text,
                "probe": "utf8",
            }
        if extension == "pdf":
            if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-1024:]:
                raise ValueError("PDF signature")
            # Limit pypdf's decompression, tree traversal and object allocation. This is
            # structural inspection, not a guarantee about an external PDF reader.
            with pypdf.apply_configuration(
                disable_legacy_handling=True,
                maximum_declared_stream_length=4 * 1024 * 1024,
                zlib_maximum_output_length=4 * 1024 * 1024,
                stream_decoding_work_maximum_length=16 * 1024 * 1024,
                stream_parsing_work_maximum_count=100000,
                array_maximum_element_count=10000,
                dictionary_maximum_element_count=10000,
                page_tree_maximum_entries=2000,
                page_tree_maximum_depth=32,
            ):
                reader = pypdf.PdfReader(
                    io.BytesIO(raw),
                    strict=True,
                    root_object_recovery_limit=0,
                    xref_stream_maximum_entries=100000,
                )
                if reader.is_encrypted:
                    raise ValueError("encrypted PDF")
                visited = set()
                count = 0
                stack = [(reader.trailer, 0)]
                forbidden = {
                    "/JavaScript",
                    "/JS",
                    "/AA",
                    "/OpenAction",
                    "/Launch",
                    "/EmbeddedFiles",
                    "/RichMedia",
                    "/XFA",
                    "/SubmitForm",
                    "/ImportData",
                    "/GoToR",
                }
                while stack:
                    obj, depth = stack.pop()
                    count += 1
                    if count > 50000 or depth > 64:
                        raise ValueError("complex PDF")
                    if isinstance(obj, IndirectObject):
                        marker = (obj.idnum, obj.generation)
                        if marker in visited:
                            continue
                        visited.add(marker)
                        obj = obj.get_object()
                    if isinstance(obj, DictionaryObject):
                        if any(
                            str(k) in forbidden or (isinstance(v, NameObject) and str(v) in forbidden)
                            for k, v in obj.items()
                        ):
                            raise ValueError("active PDF")
                        stack.extend((v, depth + 1) for v in obj.values())
                    elif isinstance(obj, ArrayObject):
                        stack.extend((v, depth + 1) for v in obj)
                if len(reader.pages) > 1000:
                    raise ValueError("too many pages")
            return {"extension": "pdf", "mime": "application/pdf", "text": "", "probe": "pdf-structural"}
        if extension == "docx":
            if not raw.startswith(b"PK"):
                raise ValueError("DOCX signature")
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                entries = z.infolist()
                if (
                    len(entries) > 200
                    or len({e.filename for e in entries}) != len(entries)
                    or sum(e.file_size for e in entries) > 16 * 1024 * 1024
                ):
                    raise ValueError("archive limit")
                texts = []
                for item in entries:
                    path = PurePosixPath(item.filename)
                    if (
                        path.is_absolute()
                        or any(p in (".", "..") for p in item.filename.split("/"))
                        or "\\" in item.filename
                        or ":" in item.filename
                        or item.flag_bits & 1
                        or item.file_size > 4 * 1024 * 1024
                        or item.file_size > max(1024, item.compress_size * 200)
                    ):
                        raise ValueError("archive path/ratio")
                    lower = item.filename.lower()
                    if re.search(r"vba|activex|embeddings|customui|oleobject", lower):
                        raise ValueError("active DOCX")
                    if lower.endswith((".xml", ".rels")):
                        content = z.read(item)
                        if b"<!doctype" in content.lower() or b"<!entity" in content.lower():
                            raise ValueError("DTD")
                        root = ET.fromstring(content)
                        for node in root.iter():
                            if node.attrib.get("TargetMode", "").lower() == "external":
                                raise ValueError("external DOCX relationship")
                        if lower == "word/document.xml":
                            texts.extend(n.text or "" for n in root.iter() if n.tag.endswith("}t"))
                if "word/document.xml" not in z.namelist() or "[Content_Types].xml" not in z.namelist():
                    raise ValueError("DOCX parts")
                text = "\n".join(texts)
                if len(text) > MAX_TEXT:
                    raise ValueError("text limit")
            return {
                "extension": "docx",
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "text": text,
                "probe": "docx-bounded",
            }
    except (
        ValueError,
        UnicodeError,
        RecursionError,
        zipfile.BadZipFile,
        ET.ParseError,
        pypdf.errors.PyPdfError,
        KeyError,
        TypeError,
    ) as exc:
        raise AppError(
            "invalid_material",
            "Документ повреждён, содержит неподдерживаемые активные элементы или превышает пределы проверки.",
            422,
        ) from exc
    raise AppError(
        "unsupported_material",
        "Поддерживаются UTF-8 TXT/MD/JSON, PDF и DOCX без активных частей; изображения импортируйте через медиабиблиотеку.",
        422,
    )

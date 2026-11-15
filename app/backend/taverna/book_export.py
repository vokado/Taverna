"""Standalone UTF-8 book exports. No user HTML, scripts or filesystem names."""

from __future__ import annotations

import base64
import html
import io
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from PIL import Image

from .core import AppError
from .manuscript_models import Manuscript

MAX_EXPORT = 64 * 1024 * 1024


def escaped_md(value):
    return re.sub(r"([\\`*_{}\[\]()#+.!<>|~-])", r"\\\1", value)


class BookExporter:
    def __init__(self, service):
        self.service, self.media = service, service.media

    def export(self, mid, fmt, expected):
        book = self.service.get(mid)
        if book["revision"] != expected:
            raise AppError("manuscript_conflict", "Рукопись изменилась; повторите экспорт.", 409)
        data = Manuscript.model_validate(book["payload"]).model_dump(mode="json")
        assets, total = {}, len(str(data).encode("utf-8"))
        for chapter in data["chapters"]:
            for item in chapter["media"]:
                aid = item["asset_id"]
                if aid in assets:
                    continue
                meta, path = self.media.checked_file(aid)
                raw = path.read_bytes()
                mime = meta["payload"]["type"]
                ext = meta["payload"]["relative_path"].rsplit(".", 1)[-1]
                if fmt == "epub":
                    if mime.startswith("image/") and mime not in ("image/png", "image/jpeg", "image/gif"):
                        with Image.open(io.BytesIO(raw)) as image:
                            out = io.BytesIO()
                            image.convert("RGBA").save(out, format="PNG")
                            raw, mime, ext = out.getvalue(), "image/png", "png"
                    elif mime.startswith("audio/") and mime != "audio/mpeg":
                        raise AppError(
                            "epub_audio_unsupported",
                            "EPUB поддерживает встроенное MP3; преобразуйте этот аудиоресурс отдельно или уберите его из рукописи.",
                            422,
                        )
                total += len(raw)
                if total > MAX_EXPORT:
                    raise AppError("book_export_limit", "Пакет превышает64МиБ; уменьшите число медиа.", 413)
                assets[aid] = {"raw": raw, "mime": mime, "name": aid + "." + ext}
        if fmt == "txt":
            chunks = [data["title"], data["author"]]
            for c in data["chapters"]:
                chunks.extend(
                    [
                        c["title"],
                        c["text"],
                        *[f"{link['label']}: {link['target']}" for link in c["links"]],
                        *[f"[Медиа: {m['caption'] or m['asset_id']}]" for m in c["media"]],
                    ]
                )
            return "\n\n".join(chunks).encode("utf-8"), "text/plain; charset=utf-8", "book.txt"
        if fmt == "markdown":
            parts = ["# " + escaped_md(data["title"]), escaped_md(data["author"])]
            for c in data["chapters"]:
                parts.extend(
                    [f'<a id="chapter-{c["id"]}"></a>', "## " + escaped_md(c["title"]), escaped_md(c["text"])]
                )
                parts.extend(
                    f"[{escaped_md(link['label'])}]({link['target'].replace('(', '%28').replace(')', '%29').replace('<', '%3C').replace('>', '%3E')})"
                    for link in c["links"]
                )
                for m in c["media"]:
                    a = assets[m["asset_id"]]
                    parts.append(
                        ("!" if a["mime"].startswith("image/") else "")
                        + f"[{escaped_md(m['caption'])}](media/{a['name']})"
                    )
            files = {
                "book.md": "\n\n".join(parts).encode("utf-8"),
                **{"media/" + a["name"]: a["raw"] for a in assets.values()},
            }
            return self.zip(files), "application/zip", "book-markdown.zip"
        if fmt == "html":
            sections = []
            for c in data["chapters"]:
                sections.append(self.chapter(c, assets, standalone=True))
            body = f"<h1>{html.escape(data['title'])}</h1><p>{html.escape(data['author'])}</p>" + "".join(
                sections
            )
            raw = (
                f'<!doctype html><html lang="{data["language"]}"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src data:; media-src data:; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;"><title>{html.escape(data["title"])}</title></head><body>{body}</body></html>'
            ).encode("utf-8")
            if len(raw) > MAX_EXPORT:
                raise AppError("book_export_limit", "HTML превышает64МиБ.", 413)
            return raw, "text/html; charset=utf-8", "book.html"
        if fmt != "epub":
            raise AppError("export_format", "Неизвестный формат экспорта.", 422)
        return self.epub(book, data, assets), "application/epub+zip", "book.epub"

    @staticmethod
    def chapter(c, assets, standalone=False):
        value = f'<section id="chapter-{c["id"]}"><h2>{html.escape(c["title"])}</h2>'
        value += "".join(
            "<p>" + html.escape(p).replace("\n", "<br />") + "</p>" for p in c["text"].split("\n\n")
        )
        for link in c["links"]:
            target = link["target"]
            if not standalone and target.startswith("#chapter-"):
                target = target[1:] + ".xhtml" + target
            value += f'<p><a href="{html.escape(target, quote=True)}" rel="noreferrer noopener">{html.escape(link["label"])}</a></p>'
        for m in c["media"]:
            a = assets[m["asset_id"]]
            src = (
                "data:" + a["mime"] + ";base64," + base64.b64encode(a["raw"]).decode()
                if standalone
                else "media/" + a["name"]
            )
            caption = html.escape(m["caption"])
            if a["mime"].startswith("image/"):
                value += (
                    f'<figure><img src="{src}" alt="{caption}" /><figcaption>{caption}</figcaption></figure>'
                )
            else:
                value += f'<figure><audio controls="controls" src="{src}">Аудио: {caption}</audio><figcaption>{caption}</figcaption></figure>'
        return value + "</section>"

    @staticmethod
    def zip(files):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        return out.getvalue()

    def epub(self, book, data, assets):
        xhtml = "http://www.w3.org/1999/xhtml"
        files = {
            "META-INF/container.xml": b'<?xml version="1.0" encoding="utf-8"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml" /></rootfiles></container>'
        }
        package = ET.Element(
            "package",
            {
                "xmlns": "http://www.idpf.org/2007/opf",
                "version": "3.0",
                "unique-identifier": "book-id",
                "xml:lang": data["language"],
            },
        )
        metadata = ET.SubElement(package, "metadata", {"xmlns:dc": "http://purl.org/dc/elements/1.1/"})
        for name, text, attrs in [
            ("identifier", "urn:uuid:" + book["id"], {"id": "book-id"}),
            ("title", data["title"], {}),
            ("language", data["language"], {}),
            ("creator", data["author"], {}),
        ]:
            if name == "creator" and not text.strip():
                continue
            ET.SubElement(metadata, "dc:" + name, attrs).text = text
        ET.SubElement(metadata, "meta", {"property": "dcterms:modified"}).text = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = ET.SubElement(package, "manifest")
        spine = ET.SubElement(package, "spine")
        ET.SubElement(
            manifest,
            "item",
            {"id": "nav", "href": "nav.xhtml", "media-type": "application/xhtml+xml", "properties": "nav"},
        )
        nav = []
        # EPUB requires a nonempty spine. An empty manuscript gets a title page.
        chapters = data["chapters"] or [
            {"id": book["id"], "title": data["title"], "text": "", "links": [], "media": []}
        ]
        for c in chapters:
            name = "chapter-" + c["id"] + ".xhtml"
            body = self.chapter(c, assets)
            text = f'<html xmlns="{xhtml}" lang="{data["language"]}" xml:lang="{data["language"]}"><head><title>{html.escape(c["title"])}</title></head><body>{body}</body></html>'
            ET.fromstring(text)
            files["EPUB/" + name] = text.encode("utf-8")
            attrs = {"id": "c" + c["id"], "href": name, "media-type": "application/xhtml+xml"}
            ET.SubElement(manifest, "item", attrs)
            ET.SubElement(spine, "itemref", {"idref": "c" + c["id"]})
            nav.append(f'<li><a href="{name}">{html.escape(c["title"])}</a></li>')
        files["EPUB/nav.xhtml"] = (
            f'<html xmlns="{xhtml}" xmlns:epub="http://www.idpf.org/2007/ops" lang="{data["language"]}" xml:lang="{data["language"]}"><head><title>Оглавление</title></head><body><nav epub:type="toc"><h1>Оглавление</h1><ol>{"".join(nav)}</ol></nav></body></html>'
        ).encode("utf-8")
        for aid, a in assets.items():
            files["EPUB/media/" + a["name"]] = a["raw"]
            ET.SubElement(
                manifest, "item", {"id": "m" + aid, "href": "media/" + a["name"], "media-type": a["mime"]}
            )
        files["EPUB/package.opf"] = ET.tostring(package, encoding="utf-8", xml_declaration=True)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            for name, raw in files.items():
                archive.writestr(name, raw, compress_type=zipfile.ZIP_DEFLATED)
        return out.getvalue()

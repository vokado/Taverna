"""Strictly local, owner-approved network broker for one outbound request.

No DNS, proxy, redirects, cookies, auth headers or internet hosts are used.
The owner grants the entire exact URL; effects are never automatically replayed.
"""

from __future__ import annotations

import base64
import ipaddress
import json
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue

from .core import AppError

_MAX_REQUEST = 8192
_MAX_RESPONSE = 16 * 1024


def validate_url(url: str) -> None:
    if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 32 for c in url):
        raise ValueError("Некорректный адрес")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in ("http", "https")
            or host is None
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("Нужен HTTP(S) адрес и числовой порт, без credentials/fragment")
        ip = ipaddress.ip_address(host)
        if not (ip.is_private or ip.is_loopback) or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError("Разрешён только локальный IP-адрес")
        expected_netloc = f"[{host}]:{port}" if ip.version == 6 else f"{host}:{port}"
        if parsed.netloc != expected_netloc:
            raise ValueError("Нужен однозначный IP:port без DNS и альтернативных форм")
        if len(parsed.path) > 1000 or len(parsed.query) > 1000:
            raise ValueError("Слишком длинный запрос")
    except (TypeError, ValueError) as exc:
        raise ValueError("Только точный локальный IP и явный порт; DNS/redirects запрещены.") from exc


def encode_request(method: str, payload: JsonValue | None):
    if method == "GET":
        if payload is not None:
            raise ValueError("GET не содержит тело")
        return None
    if method != "POST" or payload is None:
        raise ValueError("Разрешён POST с JSON")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(data) > _MAX_REQUEST:
        raise ValueError("Тело запроса больше 8 КиБ")
    return data


async def execute(request: dict, cancel) -> dict:
    method = request["method"]
    url = request["url"]
    validate_url(url)
    body = encode_request(method, request.get("payload"))
    if cancel.is_set():
        raise AppError("plugin_cancelled", "Запрос отменён до открытия соединения.", 409)
    try:
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(request["timeout_seconds"]),
        ) as client:
            async with client.stream(
                method,
                url,
                content=body,
                headers={"Content-Type": "application/json"} if body is not None else {},
            ) as response:
                if response.is_redirect:
                    raise AppError("network_redirect_forbidden", "Перенаправления запрещены.", 409)
                chunks = bytearray()
                async for part in response.aiter_bytes():
                    if cancel.is_set():
                        raise AppError("plugin_cancelled", "Запрос отменён во время чтения.", 409)
                    if len(chunks) + len(part) > _MAX_RESPONSE:
                        raise AppError("network_response_limit", "Ответ превышает 16 КиБ.", 413)
                    chunks.extend(part)
                return {
                    "state": "observed",
                    "status_code": response.status_code,
                    "content_type": (response.headers.get("content-type") or "")[:160],
                    "body_base64": base64.b64encode(chunks).decode("ascii"),
                    "bytes": len(chunks),
                    "redirected": False,
                }
    except httpx.HTTPError as exc:
        raise AppError("network_request_unknown", "Исход сетевого запроса неизвестен; не повторяйте его автоматически.", 503) from exc

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from uuid import uuid4

from starlette.responses import JSONResponse

from .core import Config


@dataclass
class Session:
    csrf: str
    expires: float


class Sessions:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.bootstraps: dict[str, float] = {}

    def bootstrap(self) -> str:
        now = time.monotonic()
        self.bootstraps = {k: v for k, v in self.bootstraps.items() if v > now}
        token = secrets.token_urlsafe(32)
        self.bootstraps[token] = now + 60
        return token

    def exchange(self, token: str) -> tuple[str, Session] | None:
        expires = self.bootstraps.pop(token, 0)
        if expires < time.monotonic():
            return None
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires > now}
        sid = secrets.token_urlsafe(32)
        session = Session(secrets.token_urlsafe(32), now + 12 * 3600)
        self.sessions[sid] = session
        return sid, session

    def get(self, cookie: str) -> Session | None:
        try:
            values = SimpleCookie(cookie)
            sid = values.get("taverna_session")
            session = self.sessions.get(sid.value if sid else "")
            if session and session.expires > time.monotonic():
                return session
        except Exception:
            pass
        return None


class LocalSecurity:
    """Bounded body before dispatch, strict host/origin and session/CSRF enforcement."""

    def __init__(self, app, config: Config, sessions: Sessions):
        self.app, self.config, self.sessions = app, config, sessions

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        method, path = scope["method"], scope["path"]
        origin = headers.get(b"origin", b"").decode("latin1")
        fetch_site = headers.get(b"sec-fetch-site", b"")

        async def reject(code, message, status):
            response = JSONResponse({"code": code, "message_ru": message,
                                     "request_id": str(uuid4()),
                                     "recoverable": True, "details": {}}, status_code=status)
            await response(scope, receive, safe_send)

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                ]
            await send(message)

        if headers.get(b"host", b"") != f"127.0.0.1:{self.config.port}".encode():
            return await reject("invalid_host", "Недопустимый адрес сервера.", 403)
        if (origin and origin != self.config.origin) or fetch_site == b"cross-site":
            return await reject("invalid_origin", "Доступ с другого сайта запрещён.", 403)
        public = path in ("/api/v1/health", "/api/v1/session/bootstrap", "/api/v1/launcher/open")
        if path.startswith("/api/") and not public:
            session = self.sessions.get(headers.get(b"cookie", b"").decode("latin1"))
            if not session:
                return await reject("session_required", "Откройте приложение через Таверна.exe.", 401)
            if method not in ("GET", "HEAD", "OPTIONS"):
                csrf = headers.get(b"x-taverna-csrf", b"").decode("latin1")
                if not secrets.compare_digest(csrf, session.csrf):
                    return await reject("csrf_required", "Запрос не прошёл проверку безопасности.", 403)
        # Buffer up to the limit, including requests without Content-Length.
        if method not in ("GET", "HEAD", "OPTIONS"):
            body, size = [], 0
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                chunk = event.get("body", b"")
                size += len(chunk)
                if size > self.config.max_body:
                    return await reject("request_too_large", "Слишком большой запрос.", 413)
                body.append(chunk)
                if not event.get("more_body", False):
                    break
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": b"".join(body), "more_body": False}
                return await receive()

            return await self.app(scope, bounded_receive, safe_send)
        return await self.app(scope, receive, safe_send)

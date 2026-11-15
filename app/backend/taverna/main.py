from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from . import __version__
from .characters import CharacterService
from .chat import ChatService
from .chat_api import router as chat_router
from .chat_import import router as chat_import_router
from .core import AppError, Config
from .director import DirectorService
from .director_api import router as director_router
from .director_drafts import router as director_drafts_router
from .generation_api import GenerationService
from .memory import MemoryService
from .memory_api import router as memory_router
from .rpg import RpgService
from .rpg_api import router as rpg_router
from .rpg_drafts import router as rpg_drafts_router
from .security import LocalSecurity, Sessions
from .settings import SettingsService
from .storage import JsonStore
from .story_package_api import router as story_package_router
from .transfers import router as transfer_router
from .world_api import router as world_router
from .worlds import WorldService


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)


def create_app(config: Config, shutdown=None, launcher_key: str | None = None) -> FastAPI:
    sessions = Sessions()
    store = JsonStore(config.root)
    generation = GenerationService(store)
    app = FastAPI(
        title="Таверна",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=generation.lifespan,
    )
    app.state.sessions = sessions
    app.state.config = config
    app.state.store = store
    app.state.generation = generation
    app.include_router(generation.router())
    app.include_router(generation.profiles.router())
    app.include_router(SettingsService(store).router())
    app.include_router(CharacterService(store, generation).router())
    app.state.chat = ChatService(store, generation)
    app.include_router(chat_router(app.state.chat))
    app.state.memory = MemoryService(app.state.chat)
    app.include_router(memory_router(app.state.memory))
    app.include_router(chat_import_router(app.state.chat))
    app.include_router(transfer_router(app.state.chat))
    app.include_router(story_package_router(app.state.chat))
    app.state.worlds = WorldService(app.state.chat)
    app.state.chat.state_reducer = app.state.worlds.message_state
    app.include_router(world_router(app.state.worlds))
    app.state.rpg = RpgService(app.state.worlds)
    app.include_router(rpg_router(app.state.rpg))
    app.include_router(rpg_drafts_router(app.state.rpg))
    app.state.director = DirectorService(app.state.memory, app.state.worlds)
    app.include_router(director_router(app.state.director))
    app.include_router(director_drafts_router(app.state.director))
    app.add_middleware(LocalSecurity, config=config, sessions=sessions)

    def error(code: str, message: str, status: int, details=None):
        return JSONResponse(
            {
                "code": code,
                "message_ru": message,
                "request_id": str(uuid4()),
                "recoverable": status < 500,
                "details": details or {},
            },
            status_code=status,
        )

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return error(exc.code, exc.message, exc.status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(
            "validation_error",
            "Проверьте формат и обязательные поля запроса.",
            422,
            {"fields": [".".join(map(str, item["loc"])) for item in exc.errors()]},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(
            "not_found" if exc.status_code == 404 else "http_error",
            "Ресурс не найден." if exc.status_code == 404 else "Запрос отклонён.",
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Do not include request bodies, credentials, filesystem paths or exception text.
        logging.getLogger("taverna").error("Внутренняя ошибка: %s", type(exc).__name__)
        return error("internal_error", "Внутренняя ошибка. Откройте журнал диагностики.", 500)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ready"}

    @app.post("/api/v1/session/bootstrap")
    def bootstrap(body: BootstrapRequest):
        result = sessions.exchange(body.token)
        if result is None:
            raise AppError("bootstrap_expired", "Ссылка запуска истекла. Запустите Таверну повторно.", 401)
        sid, session = result
        response = JSONResponse({"csrf": session.csrf})
        response.set_cookie("taverna_session", sid, httponly=True, samesite="strict", max_age=43200)
        return response

    @app.get("/api/v1/session")
    def session(request: Request):
        current = sessions.get(request.headers.get("cookie", ""))
        return {"csrf": current.csrf}

    @app.post("/api/v1/launcher/open")
    def launcher_open(request: Request):
        supplied = request.headers.get("X-Taverna-Launcher", "")
        if not launcher_key or not secrets.compare_digest(supplied, launcher_key):
            raise AppError("launcher_auth_required", "Запускатель не прошёл проверку.", 403)
        return {"url": config.origin + "/#launch=" + sessions.bootstrap()}

    @app.get("/api/v1/status")
    def status():
        return {
            "name": "Таверна",
            "version": __version__,
            "release_ready": False,
            "stage": "10",
            "message_ru": "Внутренняя сборка. Разработка полного релиза 1.0.",
        }

    @app.get("/api/v1/capabilities")
    def capabilities():
        return {
            "features": {
                "local_security": True,
                "json_storage": True,
                "graceful_shutdown": shutdown is not None,
            },
            "generation": {
                "adapter_implemented": True,
                "available": None,
                "reason_ru": "Проверьте соединение с LM Studio. Модель должна быть загружена вручную.",
            },
        }

    @app.get("/api/v1/diagnostics/storage")
    def storage_diagnostics():
        return app.state.store.integrity()

    @app.post("/api/v1/storage/integrity-check")
    def storage_integrity_check():
        return app.state.store.integrity()

    @app.post("/api/v1/system/reopen")
    def reopen():
        # Local launcher authenticates with a one-use bootstrap, then this standard CSRF API.
        return {"url": config.origin + "/#launch=" + sessions.bootstrap()}

    @app.get("/api/v1/openapi.json")
    def openapi():
        return app.openapi()

    @app.post("/api/v1/system/shutdown")
    async def stop():
        if shutdown is None:
            raise AppError(
                "shutdown_unavailable", "Управление завершением доступно только в приложении.", 409
            )
        await generation.queue.close()
        asyncio.get_running_loop().call_later(0.3, shutdown)
        return {"message_ru": "Приложение завершает работу. Сохранённые данные останутся на диске."}

    @app.get("/")
    def home():
        index = config.static / "index.html"
        if not index.is_file():
            raise AppError(
                "frontend_missing",
                "Файлы интерфейса отсутствуют. Пересоберите или восстановите приложение.",
                503,
            )
        return FileResponse(index)

    assets: Path = config.static / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    return app

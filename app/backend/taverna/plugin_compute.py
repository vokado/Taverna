"""Managed compute subprocess. Effects are handled separately by the permission broker."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from .core import AppError
from .plugin_worker import MAX_ENVELOPE, MAX_INPUT, MAX_MODULE


def worker_command():
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().parent / "Taverna-PluginHost.exe")]
    return [sys.executable, "-m", "taverna.plugin_worker"]


async def _read(stream, limit):
    parts = []
    size = 0
    while True:
        part = await stream.read(65536)
        if not part:
            return b"".join(parts)
        size += len(part)
        if size > limit:
            raise AppError("plugin_output_limit", "Расширение превысило предел вывода.", 422)
        parts.append(part)


async def compute(
    code,
    request,
    *,
    runtime="wasm",
    trusted_full_access=False,
    fuel=1000000,
    timeout=5,
    cancel=None,
    entry_path=None,
):
    if len(code) > MAX_MODULE or not 0.1 <= timeout <= 30:
        raise AppError("plugin_resource_limit", "Превышен размер модуля или допустимый deadline.", 422)
    if (
        runtime not in ("wasm", "trusted_python")
        or runtime == "trusted_python"
        and trusted_full_access is not True
    ):
        raise AppError(
            "plugin_trust_required", "Python требует явного доверия и предупреждения о полном доступе.", 403
        )
    if entry_path is not None and (
        runtime != "trusted_python" or not trusted_full_access or not Path(entry_path).is_absolute()
    ):
        raise AppError("plugin_trust_required", "Путь Python entry разрешён только доверенному host.", 403)
    if cancel and cancel.is_set():
        raise AppError("plugin_cancelled", "Вычисление расширения отменено.", 409)
    try:
        input_size = len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if input_size > MAX_INPUT:
            raise AppError("plugin_input_limit", "Вход расширения больше256КиБ.", 413)
        envelope = json.dumps(
            {
                "runtime": runtime,
                "code": base64.b64encode(code).decode("ascii"),
                "input": request,
                "fuel": fuel,
                "trusted_full_access": trusted_full_access,
                **({"entry_path": str(entry_path)} if entry_path is not None else {}),
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, UnicodeError, TypeError) as exc:
        raise AppError(
            "plugin_input_invalid", "Вход расширения должен быть корректным UTF-8 JSON.", 422
        ) from exc
    if len(envelope) > MAX_ENVELOPE:
        raise AppError("plugin_input_limit", "Вход расширения слишком велик.", 413)
    environment = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "VIRTUAL_ENV"}
    }
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        process = await asyncio.create_subprocess_exec(
            *worker_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            **flags,
        )
    except OSError as exc:
        raise AppError(
            "plugin_host_unavailable", "Не удалось запустить отдельный host расширения.", 503
        ) from exc
    output = asyncio.create_task(_read(process.stdout, MAX_ENVELOPE))
    errors = asyncio.create_task(_read(process.stderr, 8192))

    async def feed():
        try:
            process.stdin.write(envelope)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    input_task = asyncio.create_task(feed())
    waiting = asyncio.create_task(process.wait())
    tasks = (output, errors, input_task, waiting)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not all(task.done() for task in tasks):
            if cancel and cancel.is_set():
                raise AppError(
                    "plugin_cancelled",
                    "Вычисление расширения отменено; уже выполненные действия доверенного Python не отменяются.",
                    409,
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise AppError("plugin_timeout", "Host расширения превысил время исполнения.", 408)
            for task in tasks:
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
            await asyncio.sleep(0.03)
        if process.returncode != 0:
            raise AppError(
                "plugin_host_crashed", "Отдельный host завершился аварийно; сервер продолжает работу.", 422
            )
        try:
            result = json.loads(output.result())
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("protocol")
        except (ValueError, UnicodeError) as exc:
            raise AppError(
                "plugin_protocol_invalid", "Host вернул недопустимый ответ; действия не применены.", 422
            ) from exc
        if not result["ok"]:
            raise AppError(
                result.get("code", "plugin_execution_failed"),
                result.get("message_ru", "Ошибка расширения."),
                422,
            )
        return {
            **result,
            "isolation": "wasm-no-imports-v1" if runtime == "wasm" else "trusted-full-access-process",
        }
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(asyncio.shield(waiting), timeout=1)
        except (TimeoutError, asyncio.CancelledError):
            # A trusted child may retain an inherited pipe. It is not sandboxed;
            # release our handles rather than waiting indefinitely on descendants.
            process._transport.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

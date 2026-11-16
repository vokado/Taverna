"""Separate, bounded compute host. Untrusted WASM gets no imports and no WASI.

This module deliberately imports neither application storage nor API services.
Trusted Python is an explicit full-access mode, not a sandbox.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import sys

MAX_MODULE = 256 * 1024
MAX_INPUT = 256 * 1024
MAX_OUTPUT = 256 * 1024
MAX_ENVELOPE = 1024 * 1024


class HostError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def binary_budget(code):
    if not code.startswith(b"\x00asm\x01\x00\x00\x00") or len(code) > MAX_MODULE:
        raise HostError(
            "plugin_module_invalid", "Модуль не является поддерживаемым WASM v1 или превышает256КиБ."
        )

    def leb(position, end):
        value = 0
        for shift in range(0, 35, 7):
            if position >= end:
                raise HostError("plugin_module_invalid", "Повреждённая длина секции WASM.")
            byte = code[position]
            position += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value, position
        raise HostError("plugin_module_invalid", "Некорректное целое WASM.")

    position = 8
    while position < len(code):
        kind = code[position]
        position += 1
        length, position = leb(position, len(code))
        end = position + length
        if end > len(code):
            raise HostError("plugin_module_invalid", "Секция выходит за границы модуля.")
        if kind in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12):
            count, _ = leb(position, end)
            limit = 0 if kind == 2 else 1 if kind in (4, 5) else 256
            if count > limit:
                raise HostError(
                    "plugin_import_denied" if kind == 2 else "plugin_module_limit",
                    "Импорты WASM запрещены; превышен предел секций/функций/памяти/таблиц.",
                )
        position = end


def run_wasm(code, request, fuel):
    import wasmtime

    binary_budget(code)
    config = wasmtime.Config()
    config.consume_fuel = True
    config.wasm_threads = False
    config.wasm_memory64 = False
    config.wasm_multi_memory = False
    config.shared_memory = False
    config.parallel_compilation = False
    config.max_wasm_stack = 512 * 1024
    config.memory_reservation = 16 * 1024 * 1024
    config.memory_guard_size = 64 * 1024
    engine = wasmtime.Engine(config)
    with wasmtime.Store(engine) as store:
        store.set_fuel(fuel)
        store.set_limits(memory_size=16 * 1024 * 1024, instances=1, memories=1, tables=1, table_elements=1000)
        module = wasmtime.Module(engine, code)
        if module.imports:
            raise HostError("plugin_import_denied", "WASM imports не разрешены.")
        instance = wasmtime.Linker(engine).instantiate(store, module)
        exports = instance.exports(store)
        memory, allocate, run = (exports.get(key) for key in ("memory", "alloc", "run"))
        if (
            not isinstance(memory, wasmtime.Memory)
            or not isinstance(allocate, wasmtime.Func)
            or not isinstance(run, wasmtime.Func)
        ):
            raise HostError("plugin_abi_invalid", "Нужны memory, alloc(i32)->i32 и run(i32,i32)->i64.")
        alloc_type, run_type = allocate.type(store), run.type(store)
        if (
            [str(x) for x in alloc_type.params] != ["i32"]
            or [str(x) for x in alloc_type.results] != ["i32"]
            or [str(x) for x in run_type.params] != ["i32", "i32"]
            or [str(x) for x in run_type.results] != ["i64"]
        ):
            raise HostError("plugin_abi_invalid", "Сигнатура функций не соответствует ABI v1.")
        raw = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_INPUT:
            raise HostError("plugin_input_limit", "Вход расширения больше256КиБ.")
        pointer = allocate(store, len(raw))
        if pointer < 0 or pointer + len(raw) > memory.data_len(store):
            raise HostError("plugin_pointer_invalid", "Модуль выделил недопустимую область входа.")
        memory.write(store, raw, pointer)
        packed = run(store, pointer, len(raw))
        pointer, length = ((packed & 0xFFFFFFFFFFFFFFFF) >> 32, packed & 0xFFFFFFFF)
        if not length or length > MAX_OUTPUT or pointer + length > memory.data_len(store):
            raise HostError("plugin_output_limit", "Недопустимая область или размер результата расширения.")
        result = json.loads(
            bytes(memory.read(store, pointer, pointer + length)).decode("utf-8"),
            parse_constant=lambda v: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
        if not isinstance(result, dict):
            raise HostError("plugin_output_invalid", "Результат должен быть JSON-объектом.")
        return {"result": result, "fuel_used": fuel - store.get_fuel()}


class BoundedLog(io.StringIO):
    def write(self, value):
        # Trusted code may bypass Python stdout; the parent also bounds the OS pipe.
        remaining = 8192 - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        return len(value)


def run_trusted(code, request, authorized):
    if authorized is not True:
        raise HostError(
            "plugin_trust_required", "Python исполняется только после явного подтверждения полного доступа."
        )
    log = BoundedLog()
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        namespace = {"__name__": "taverna_trusted_plugin"}
        exec(compile(code.decode("utf-8"), "<trusted-plugin>", "exec"), namespace)
        function = namespace.get("run")
        if not callable(function):
            raise HostError("plugin_abi_invalid", "Доверенный Python должен экспортировать run(request).")
        result = function(request)
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if not isinstance(result, dict) or len(raw) > MAX_OUTPUT:
            raise HostError("plugin_output_limit", "Результат должен быть JSON-объектом до256КиБ.")
    return {"result": result, "trusted_full_access": True, "log": log.getvalue()}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_ENVELOPE + 1)
        if len(raw) > MAX_ENVELOPE:
            raise HostError("plugin_input_limit", "Превышен размер сообщения host.")
        envelope = json.loads(raw)
        if set(envelope) - {"runtime", "code", "input", "fuel", "trusted_full_access"} or not isinstance(
            envelope.get("input"), dict
        ):
            raise HostError("plugin_input_invalid", "Неверный формат сообщения host.")
        code = base64.b64decode(envelope["code"], validate=True)
        if len(code) > MAX_MODULE:
            raise HostError("plugin_module_limit", "Модуль больше256КиБ.")
        fuel = envelope.get("fuel", 1000000)
        if type(fuel) is not int or not 10000 <= fuel <= 10000000:
            raise HostError("plugin_fuel_limit", "Недопустимый вычислительный бюджет.")
        if envelope["runtime"] == "wasm":
            result = run_wasm(code, envelope["input"], fuel)
        elif envelope["runtime"] == "trusted_python":
            result = run_trusted(code, envelope["input"], envelope.get("trusted_full_access"))
        else:
            raise HostError("plugin_runtime_invalid", "Неизвестный runtime расширения.")
        response = {"ok": True, **result}
    except HostError as exc:
        response = {"ok": False, "code": exc.code, "message_ru": exc.message}
    except BaseException as exc:
        # No source/inputs/paths/traceback from guest code in the public protocol.
        response = {
            "ok": False,
            "code": "plugin_execution_failed",
            "message_ru": "Расширение завершилось с ошибкой или превысило вычислительные ограничения.",
            "error_type": type(exc).__name__,
        }
    encoded = json.dumps(response, ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(encoded) > MAX_ENVELOPE:
        encoded = json.dumps(
            {
                "ok": False,
                "code": "plugin_output_limit",
                "message_ru": "Превышен размер сообщения результата.",
            },
            ensure_ascii=True,
        ).encode("ascii")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

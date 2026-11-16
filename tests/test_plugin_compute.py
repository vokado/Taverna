import asyncio
import json

import pytest
import wasmtime
from taverna.core import AppError
from taverna.plugin_compute import compute
from taverna.plugin_worker import HostError, binary_budget


def module(text):
    return bytes(wasmtime.wat2wasm(text))


def guest(result=None, body=None, alloc="i32.const 4096"):
    raw = json.dumps(result or {"actions": [], "message": "Привет из WASM"}, ensure_ascii=False).encode()
    encoded = "".join("\\" + format(b, "02x") for b in raw)
    return module(
        f'(module (memory (export "memory") 1 256) (data (i32.const 0) "{encoded}") (func (export "alloc") (param i32) (result i32) {alloc}) (func (export "run") (param i32 i32) (result i64) {body or f"i64.const {len(raw)}"}))'
    )


@pytest.mark.asyncio
async def test_real_subprocess_json_wasm_no_imports_and_fuel_accounting():
    result = await compute(guest(), {"parameter": "Данные владельца"})
    assert result["result"]["message"] == "Привет из WASM"
    assert result["isolation"] == "wasm-no-imports-v1" and result["fuel_used"] > 0


@pytest.mark.parametrize(
    "namespace,function",
    [
        ("wasi_snapshot_preview1", "fd_write"),
        ("wasi_snapshot_preview1", "path_open"),
        ("env", "system"),
        ("env", "fetch"),
        ("env", "write_file"),
    ],
)
@pytest.mark.asyncio
async def test_wasi_os_network_and_filesystem_imports_denied(namespace, function):
    code = module(f'(module (import "{namespace}" "{function}" (func)))')
    with pytest.raises(AppError) as error:
        await compute(code, {})
    assert error.value.code == "plugin_import_denied"


@pytest.mark.parametrize(
    "code",
    [
        b"not wasm",
        b"\0asm\1\0\0\0\1\x80",
        b"\0asm\1\0\0\0\1\xff\xff\xff\xff\xff\xff",
        b"\0asm\1\0\0\0\1\x09\0",
        b"\0asm\1\0\0\0" + b"x" * 256 * 1024,
    ],
)
def test_module_parser_bounds(code):
    with pytest.raises(HostError):
        binary_budget(code)


@pytest.mark.asyncio
async def test_loop_trap_bad_pointers_abi_and_memory_exhaustion_are_contained():
    for code in [
        guest(body="(loop br 0) i64.const 0"),
        guest(body="unreachable"),
        guest(body="i64.const 4294967295"),
        guest(alloc="i32.const -1"),
        module('(module (memory (export "memory") 512))'),
        module('(module (memory (export "memory") 1))'),
    ]:
        with pytest.raises(AppError):
            await compute(code, {}, fuel=10000)
    # Linear memory growth above the store budget fails inside the guest.
    code = guest(body="i32.const 256 memory.grow i32.const -1 i32.ne if unreachable end i64.const 14")
    # Return a known JSON object from memory after observing memory.grow failure.
    raw = b'{"actions":[]}'
    text = "".join("\\" + format(b, "02x") for b in raw)
    code = module(
        f'(module (memory (export "memory") 1) (data (i32.const 0) "{text}") (func (export "alloc") (param i32) (result i32) i32.const 4096) (func (export "run") (param i32 i32) (result i64) i32.const 256 memory.grow i32.const -1 i32.ne if unreachable end i64.const {len(raw)}))'
    )
    assert (await compute(code, {}))["result"] == {"actions": []}
    assert (await compute(guest(), {}))["result"]["actions"] == []


@pytest.mark.asyncio
async def test_trusted_python_requires_explicit_full_access_and_logs_are_bounded():
    code = b'def run(request):\n print("x"*1000000)\n return {"actions": [], "echo": request}\n'
    with pytest.raises(AppError) as error:
        await compute(code, {}, runtime="trusted_python")
    assert error.value.status == 403
    result = await compute(code, {"value": "Данные"}, runtime="trusted_python", trusted_full_access=True)
    assert result["trusted_full_access"] is True and result["isolation"] == "trusted-full-access-process"
    assert len(result["log"]) == 8192 and result["result"]["echo"] == {"value": "Данные"}


@pytest.mark.asyncio
async def test_real_host_crash_timeout_cancel_and_output_flood_do_not_crash_parent():
    with pytest.raises(AppError) as error:
        await compute(b"import os\nos._exit(12)", {}, runtime="trusted_python", trusted_full_access=True)
    assert error.value.code == "plugin_host_crashed"
    with pytest.raises(AppError) as error:
        await compute(
            b"import time\ndef run(request): time.sleep(60)",
            {},
            runtime="trusted_python",
            trusted_full_access=True,
            timeout=0.3,
        )
    assert error.value.code == "plugin_timeout"
    cancel = asyncio.Event()
    task = asyncio.create_task(
        compute(
            b"import time\ndef run(request): time.sleep(60)",
            {},
            runtime="trusted_python",
            trusted_full_access=True,
            cancel=cancel,
        )
    )
    await asyncio.sleep(0.2)
    cancel.set()
    with pytest.raises(AppError) as error:
        await task
    assert error.value.code == "plugin_cancelled"
    with pytest.raises(AppError) as error:
        await compute(
            b'import os\ndef run(request):\n os.write(1,b"x"*2000000)\n return {}',
            {},
            runtime="trusted_python",
            trusted_full_access=True,
        )
    assert error.value.code == "plugin_output_limit"
    assert (await compute(guest(), {}))["ok"] is True


@pytest.mark.asyncio
async def test_input_limit_applies_before_starting_any_runtime():
    for runtime in ("wasm", "trusted_python"):
        with pytest.raises(AppError) as error:
            await compute(
                guest(),
                {"text": "x" * (256 * 1024)},
                runtime=runtime,
                trusted_full_access=runtime == "trusted_python",
            )
        assert error.value.code == "plugin_input_limit"

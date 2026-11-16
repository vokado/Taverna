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


@pytest.mark.asyncio
async def test_os_memory_limit_is_active_before_trusted_guest_code():
    code = b'def run(request):\n try: data=bytearray(600*1024*1024)\n except MemoryError: return {"actions": [], "allocation_rejected": True}\n return {"actions": [], "allocation_rejected": False}\n'
    result = await compute(code, {}, runtime="trusted_python", trusted_full_access=True)
    assert result["result"]["allocation_rejected"] is True
    assert result["host_limits"]


@pytest.mark.asyncio
async def test_windows_job_stops_trusted_ordinary_descendant_on_host_crash(tmp_path):
    import ctypes
    import os

    if os.name != "nt":
        pytest.skip("Windows job-object descendant test requires Windows")
    pid_file = tmp_path / "descendant.pid"
    code = (
        'import subprocess,sys,os\nfrom pathlib import Path\nchild=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nPath('
        + repr(str(pid_file))
        + ").write_text(str(child.pid))\nos._exit(12)"
    ).encode()
    with pytest.raises(AppError) as error:
        await compute(code, {}, runtime="trusted_python", trusted_full_access=True)
    assert error.value.code == "plugin_host_crashed"
    pid = int(pid_file.read_text())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)


@pytest.mark.asyncio
async def test_trusted_python_entry_context_supports_packaged_helpers(tmp_path):
    folder = tmp_path / "Плагин с пробелами"
    folder.mkdir()
    (folder / "helper.py").write_text('value="проверено"', encoding="utf-8")
    code = 'import helper\ndef run(request): return {"actions": [],"value":helper.value,"entry":__file__}'.encode()
    entry = folder / "main.py"
    entry.write_bytes(code)
    result = await compute(code, {}, runtime="trusted_python", trusted_full_access=True, entry_path=entry)
    assert result["result"]["value"] == "проверено" and result["result"]["entry"] == str(entry)
    with pytest.raises(AppError):
        await compute(guest(), {}, entry_path=entry)

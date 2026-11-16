"""Real OS commands inside the real managed host, not a mocked broker/API."""

import base64
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from taverna.core import AppError
from taverna.plugin_command_worker import BRIDGE, MAX_OUTPUT, pinned_executable
from taverna.plugin_compute import compute


def command(code, *args, path=None, timeout=3):
    executable = Path(path) if path else Path(sys.executable).resolve()
    return {
        "executable": str(executable),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "argv": ["-c", code, *args],
        "timeout_seconds": timeout,
    }


async def execute(body, **kwargs):
    return (
        await compute(BRIDGE, body, runtime="trusted_python", trusted_full_access=True, timeout=8, **kwargs)
    )["result"]


@pytest.mark.asyncio
async def test_real_command_exact_argv_binary_output_and_nonzero_status(tmp_path):
    marker = tmp_path / "must-not-be-created"
    argument = f'пробелы & echo bad > "{marker}"; $(echo nope)'
    result = await execute(
        command(
            'import sys,os,json;os.write(1,json.dumps(sys.argv[1:],ensure_ascii=False).encode());os.write(2,b"\\x00\\xff");sys.exit(7)',
            argument,
        )
    )
    assert result["state"] == "exited" and result["return_code"] == 7
    assert json.loads(base64.b64decode(result["stdout_base64"])) == [argument]
    assert base64.b64decode(result["stderr_base64"]) == b"\x00\xff"
    assert not result["truncated"] and not marker.exists()


@pytest.mark.asyncio
async def test_changed_hash_rejects_before_child_side_effect(tmp_path):
    marker = tmp_path / "not-executed"
    body = command(f'from pathlib import Path;Path({str(marker)!r}).write_text("bad")')
    body["sha256"] = "0" * 64
    with pytest.raises(AppError):
        await execute(body)
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
async def test_real_output_flood_is_bounded_and_child_stopped(channel):
    fd = 1 if channel == "stdout" else 2
    result = await execute(command(f'import os,time;os.write({fd},b"x"*1000000);time.sleep(60)'))
    assert result["state"] == "output_limit" and result["truncated"]
    assert len(base64.b64decode(result[channel + "_base64"])) == MAX_OUTPUT


@pytest.mark.asyncio
async def test_real_command_timeout_retains_partial_bytes_without_claiming_no_effects(tmp_path):
    marker = tmp_path / "effect-before-timeout"
    body = command(
        f'import os,time;from pathlib import Path;Path({str(marker)!r}).write_text("effect");os.write(1,b"started");time.sleep(60)',
        timeout=2,
    )
    result = await execute(body)
    assert result["state"] == "timeout" and marker.read_text() == "effect"
    assert base64.b64decode(result["stdout_base64"]) == b"started"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "relative",
        "traversal",
        "hash",
        "nul",
        "many_args",
        "long_arg",
        "deadline_bool",
        "deadline_nan",
        "extra",
    ],
)
async def test_invalid_command_envelopes_never_launch_child(tmp_path, change):
    marker = tmp_path / "must-not-execute"
    body = command(f'from pathlib import Path;Path({str(marker)!r}).write_text("bad")')
    if change == "relative":
        body["executable"] = "python.exe"
    elif change == "traversal":
        body["executable"] = str(Path(body["executable"]).parent / ".." / "python.exe")
    elif change == "hash":
        body["sha256"] = "x" * 64
    elif change == "nul":
        body["argv"].append("nul\0arg")
    elif change == "many_args":
        body["argv"] += ["x"] * 65
    elif change == "long_arg":
        body["argv"].append("x" * 8193)
    elif change == "deadline_bool":
        body["timeout_seconds"] = True
    elif change == "deadline_nan":
        body["timeout_seconds"] = float("nan")
    else:
        body["shell"] = True
    with pytest.raises(AppError):
        await execute(body)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows share/reparse APIs")
def test_windows_pin_denies_parent_rename_leaf_replace_and_write(tmp_path):
    folder = tmp_path / "Папка с пробелами"
    folder.mkdir()
    executable = folder / "Проверка.exe"
    shutil.copyfile(sys.executable, executable)
    alias = folder / "alias.exe"
    os.link(executable, alias)
    checksum = hashlib.sha256(executable.read_bytes()).hexdigest()
    with pinned_executable(str(executable), checksum):
        with pytest.raises(OSError):
            folder.rename(tmp_path / "renamed")
        with pytest.raises(OSError):
            executable.rename(folder / "replacement.exe")
        with pytest.raises(OSError):
            executable.write_bytes(b"bad")
        with pytest.raises(OSError):
            alias.write_bytes(b"bad")
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == checksum
    folder.rename(tmp_path / "renamed")


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows implicit batch behavior")
@pytest.mark.asyncio
async def test_windows_batch_file_is_not_implicitly_passed_to_shell(tmp_path):
    executable = tmp_path / "bad.cmd"
    executable.write_text("echo unwanted")
    body = command("", path=executable)
    with pytest.raises(AppError):
        await execute(body)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object command family guarantee")
@pytest.mark.asyncio
async def test_windows_cancellation_stops_real_command_child(tmp_path):
    import asyncio
    import ctypes
    import time

    marker = tmp_path / "command.pid"
    cancel = asyncio.Event()
    body = command(
        f"import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)",
        timeout=20,
    )
    task = asyncio.create_task(execute(body, cancel=cancel))
    handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        deadline = time.monotonic() + 10
        while True:
            if task.done():
                task.result()
                pytest.fail("Host exited before command readiness")
            try:
                pid = int(marker.read_text())
                break
            except (FileNotFoundError, ValueError):
                assert time.monotonic() < deadline, "Command child did not start"
                await asyncio.sleep(0.02)
        handle = kernel.OpenProcess(0x00100000, False, pid)
        assert handle and kernel.WaitForSingleObject(handle, 0) == 258
        cancel.set()
        with pytest.raises(AppError) as error:
            await task
        assert error.value.code == "plugin_cancelled"
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        cancel.set()
        await asyncio.gather(task, return_exceptions=True)
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows reparse-point handles")
@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_windows_executable_pin_rejects_reparse_component(tmp_path, link_kind):
    source = Path(sys.executable).resolve()
    if link_kind == "file":
        target = tmp_path / "link.exe"
        try:
            target.symlink_to(source)
        except OSError:
            pytest.skip("Windows account cannot create file symlinks")
    else:
        link = tmp_path / "linked-directory"
        try:
            link.symlink_to(source.parent, target_is_directory=True)
        except OSError:
            pytest.skip("Windows account cannot create directory symlinks")
        target = link / source.name
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Reparse"):
        with pinned_executable(str(target), checksum):
            pytest.fail("Reparse executable unexpectedly pinned")


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows DOS drive mapping and volume GUID")
def test_windows_command_cannot_be_redirected_by_remapping_drive_after_pin(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    from taverna import plugin_command_worker as worker

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    kernel.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.QueryDosDeviceW.restype = wintypes.DWORD
    kernel.DefineDosDeviceW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR]
    kernel.DefineDosDeviceW.restype = wintypes.BOOL
    mask = kernel.GetLogicalDrives()
    buffer = ctypes.create_unicode_buffer(32768)
    drive = next(
        (
            f"{chr(65 + index)}:"
            for index in range(25, 15, -1)
            if not mask & (1 << index)
            and not kernel.QueryDosDeviceW(f"{chr(65 + index)}:", buffer, len(buffer))
        ),
        None,
    )
    if drive is None:
        pytest.skip("No unused test drive letter")
    original, replacement = tmp_path / "original", tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    binary = original / "trial.exe"
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe", binary)
    (replacement / "trial.exe").write_bytes(b"This must never execute")
    mapped = False
    popen = worker.subprocess.Popen
    try:
        assert kernel.DefineDosDeviceW(8, drive, str(original)), "Cannot create isolated test mapping"
        mapped = True
        body = {
            "executable": drive + "\\trial.exe",
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "argv": [],
            "timeout_seconds": 3,
        }

        def remap_then_spawn(*args, **kwargs):
            nonlocal mapped
            assert kernel.DefineDosDeviceW(2 | 8, drive, None)
            mapped = False
            assert kernel.DefineDosDeviceW(8, drive, str(replacement))
            mapped = True
            assert Path(kwargs["executable"]).resolve().is_relative_to(original.resolve())
            return popen(*args, **kwargs)

        monkeypatch.setattr(worker.subprocess, "Popen", remap_then_spawn)
        result = worker.run(body)
        assert result["state"] == "exited" and result["return_code"] == 0
    finally:
        if mapped:
            assert kernel.DefineDosDeviceW(2 | 8, drive, None), "Remove test drive mapping"


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows suspended-image verification")
def test_windows_different_image_is_killed_before_initial_thread_resume(tmp_path, monkeypatch):
    from taverna import plugin_command_worker as worker

    marker = tmp_path / "not-executed"
    body = command(f'from pathlib import Path;Path({str(marker)!r}).write_text("bad")')
    popen = worker.subprocess.Popen
    processes = []

    def hijack(*args, **kwargs):
        assert kwargs["creationflags"] & worker.CREATE_SUSPENDED
        kwargs["executable"] = str(Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe")
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", hijack)
    with pytest.raises(ValueError, match="Suspended image does not match"):
        worker.run(body)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows executable paths")
@pytest.mark.asyncio
async def test_windows_command_path_with_cyrillic_and_spaces(tmp_path):
    folder = tmp_path / "Папка с пробелами"
    folder.mkdir()
    path = folder / "Проверка.exe"
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe", path)
    body = {
        "executable": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "argv": [],
        "timeout_seconds": 3,
    }
    result = await execute(body)
    assert result["state"] == "exited" and result["return_code"] == 0
    assert result["stdout_base64"]  # Account output is not printed.

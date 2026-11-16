"""Stdlib-only command kernel for the existing managed host.

No API, registry or storage imports. Only a future owner-confirmed broker may call
this bridge; a command has full OS access and is never a WASM sandbox capability.
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import stat
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

MAX_EXECUTABLE = 256 * 1024 * 1024
MAX_OUTPUT = 32 * 1024
BRIDGE = b"from taverna.plugin_command_worker import run\n"


def validate(request):
    if not isinstance(request, dict) or set(request) != {"executable", "sha256", "argv", "timeout_seconds"}:
        raise ValueError("Invalid command envelope")
    name, argv, timeout = request["executable"], request["argv"], request["timeout_seconds"]
    if not isinstance(name, str) or not Path(name).is_absolute() or any(ord(c) < 32 for c in name):
        raise ValueError("Absolute local executable required")
    parts = re.split(r"[/\\]", name)
    if any(p in (".", "..") for p in parts) or name.startswith(("\\\\", "//")):
        raise ValueError("Ambiguous or network executable path")
    if os.name == "nt":
        if Path(name).suffix.casefold() != ".exe" or ":" in name[2:]:
            raise ValueError("Only native Windows executables, no batch/ADS")
        devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        devices.update(f"{p}{i}" for p in ("COM", "LPT") for i in (*range(10), "¹", "²", "³"))
        if any(p.endswith((".", " ")) or p.split(".")[0].upper() in devices for p in parts[1:]):
            raise ValueError("Ambiguous Windows component")
    if not isinstance(request["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", request["sha256"]):
        raise ValueError("Expected executable hash required")
    if (
        not isinstance(argv, list)
        or len(argv) > 64
        or any(not isinstance(a, str) or len(a) > 8192 or "\0" in a for a in argv)
    ):
        raise ValueError("Invalid argument vector")
    if len(subprocess.list2cmdline([name, *argv]).encode("utf-16-le")) > 60000:
        raise ValueError("Command line too large")
    if sum(len(a.encode("utf-8")) for a in [name, *argv]) > 32768:
        raise ValueError("Argument bytes too large")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.1 <= timeout <= 20:
        raise ValueError("Invalid command deadline")


@contextmanager
def pinned_executable(name, expected_sha256):
    """Keep Windows ancestors/leaf nonreplaceable; Linux exec uses a pinned fd."""
    path = Path(name)
    with ExitStack() as stack:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            class Info(ctypes.Structure):
                _fields_ = [
                    ("attributes", wintypes.DWORD),
                    ("creation", wintypes.FILETIME),
                    ("access", wintypes.FILETIME),
                    ("write", wintypes.FILETIME),
                    ("volume", wintypes.DWORD),
                    ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD),
                    ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD),
                    ("index_low", wintypes.DWORD),
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
            kernel.GetFileInformationByHandle.restype = wintypes.BOOL
            kernel.GetFileType.argtypes = [wintypes.HANDLE]
            kernel.GetFileType.restype = wintypes.DWORD
            kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
            kernel.GetDriveTypeW.restype = wintypes.UINT
            if kernel.GetDriveTypeW(path.anchor) not in (2, 3, 5, 6):
                raise ValueError("Only local drive executables")

            def open_handle(item, directory):
                # Parents permit normal child I/O but deny directory replacement.
                # The executable denies both writes and replacement, including aliases.
                handle = kernel.CreateFileW(
                    str(item),
                    0x80 if directory else 0x80000000,
                    3 if directory else 1,
                    None,
                    3,
                    0x00200000 | (0x02000000 if directory else 0),
                    None,
                )
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    info = Info()
                    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
                        raise ValueError("Reparse or wrong executable path type")
                    if not directory and kernel.GetFileType(handle) != 1:
                        raise ValueError("Executable is not a disk file")
                    return handle
                except BaseException:
                    kernel.CloseHandle(handle)
                    raise

            for parent in reversed(path.parents):
                handle = open_handle(parent, True)
                stack.callback(kernel.CloseHandle, handle)
            handle = open_handle(path, False)
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            # DOS drive mappings can change independently of directory locks.
            # Bind CreateProcess to the opened file's stable local volume path.
            kernel.GetFinalPathNameByHandleW.argtypes = [
                wintypes.HANDLE,
                wintypes.LPWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
            final_name = ctypes.create_unicode_buffer(32768)
            count = kernel.GetFinalPathNameByHandleW(handle, final_name, len(final_name), 1)
            if not count or count >= len(final_name) or not final_name.value.startswith("\\\\?\\Volume{"):
                raise OSError("Cannot pin local executable volume path")
            launch_path = final_name.value
        else:
            # Linux checks support source tests. They do not emulate Windows share
            # locks or promise protection against a concurrent in-place file writer.
            if any(p.is_symlink() for p in (*path.parents, path)):
                raise ValueError("Executable path contains a symlink")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            launch_path = f"/proc/self/fd/{stream.fileno()}"
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_EXECUTABLE:
            raise ValueError("Invalid executable type or size")
        checksum = hashlib.sha256()
        size = 0
        while data := stream.read(1024 * 1024):
            size += len(data)
            if size > MAX_EXECUTABLE:
                raise ValueError("Executable grew above limit")
            checksum.update(data)
        after = os.fstat(stream.fileno())
        if checksum.hexdigest() != expected_sha256 or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Executable no longer matches approved hash")
        yield stream, launch_path


def run(request):
    """Execute exactly the supplied vector. Nonzero/timeout never imply no effects."""
    validate(request)
    with pinned_executable(request["executable"], request["sha256"]) as (stream, launch_path):
        flags = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"pass_fds": (stream.fileno(),)}
        )
        child = subprocess.Popen(
            [request["executable"], *request["argv"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            executable=launch_path,
            # CreateProcess does not accept a volume-GUID current directory.
            # Keep the executable pinned by GUID, but inherit the managed host's
            # working directory on Windows. No guest-controlled cwd is accepted.
            cwd=None if os.name == "nt" else Path(request["executable"]).parent,
            shell=False,
            **flags,
        )
        overflow = threading.Event()
        read_error = threading.Event()
        buffers = {"stdout": bytearray(), "stderr": bytearray()}

        def read(pipe, key):
            try:
                while data := pipe.read(8192):
                    available = MAX_OUTPUT - len(buffers[key])
                    buffers[key].extend(data[:available])
                    if len(data) > available:
                        overflow.set()
            except (OSError, ValueError, MemoryError):
                read_error.set()
            finally:
                pipe.close()

        readers = [
            threading.Thread(target=read, args=(pipe, key), daemon=True)
            for pipe, key in ((child.stdout, "stdout"), (child.stderr, "stderr"))
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + request["timeout_seconds"]
        state = "exited"
        try:
            while child.poll() is None or any(reader.is_alive() for reader in readers):
                if overflow.is_set():
                    state = "output_limit"
                    break
                if time.monotonic() >= deadline:
                    state = "timeout"
                    break
                time.sleep(0.01)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=1)
            for reader in readers:
                reader.join(timeout=0.1)
        if overflow.is_set():
            state = "output_limit"
        elif read_error.is_set():
            state = "io_error"
        return {
            "state": state,
            "return_code": child.returncode,
            **{
                key + "_base64": base64.b64encode(bytes(value)).decode("ascii")
                for key, value in buffers.items()
            },
            "truncated": overflow.is_set(),
        }

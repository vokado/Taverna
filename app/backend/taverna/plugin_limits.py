"""Resource limits for the managed host before any guest code is evaluated."""

from __future__ import annotations

import os

MEMORY_LIMIT = 512 * 1024 * 1024
WINDOWS_JOB = None
# Verified against Microsoft WinSDK winnt.h (JOB_OBJECT_LIMIT_*).
PROCESS_MEMORY = 0x00000100
JOB_MEMORY = 0x00000200
KILL_ON_JOB_CLOSE = 0x00002000
ACTIVE_PROCESS = 0x00000008


def apply_limits():
    global WINDOWS_JOB
    if os.name != "nt":
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = min(MEMORY_LIMIT, hard) if hard != resource.RLIM_INFINITY else MEMORY_LIMIT
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        return {
            "address_space_bytes": limit,
            "scope": "managed process; trusted descendants are not an OS sandbox",
        }
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Counters(ctypes.Structure):
        _fields_ = [
            (key, ctypes.c_ulonglong)
            for key in [
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            ]
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", Counters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError("host job allocation failed")
    info = Extended()
    info.BasicLimitInformation.LimitFlags = PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE | ACTIVE_PROCESS
    info.BasicLimitInformation.ActiveProcessLimit = 8
    info.ProcessMemoryLimit = MEMORY_LIMIT
    info.JobMemoryLimit = MEMORY_LIMIT
    if not kernel.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    ) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        kernel.CloseHandle(job)
        raise OSError("host job resource limits could not be attached")
    # Keep this non-inheritable handle open. Worker exit/kill closes it and stops
    # ordinary descendants. Full-trust malicious code is not restricted by grants.
    WINDOWS_JOB = job
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    actual = Extended()
    if (
        not kernel.QueryInformationJobObject(job, 9, ctypes.byref(actual), ctypes.sizeof(actual), None)
        or actual.BasicLimitInformation.LimitFlags & info.BasicLimitInformation.LimitFlags
        != info.BasicLimitInformation.LimitFlags
        or actual.ProcessMemoryLimit != MEMORY_LIMIT
        or actual.JobMemoryLimit != MEMORY_LIMIT
        or actual.BasicLimitInformation.ActiveProcessLimit != 8
    ):
        raise OSError("host limits could not be confirmed")
    return {
        "process_commit_bytes": MEMORY_LIMIT,
        "family_commit_bytes": MEMORY_LIMIT,
        "process_count": 8,
        "kill_family_on_host_exit": True,
    }

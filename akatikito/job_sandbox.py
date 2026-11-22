"""Win32 Job Object process-tree isolation and resource limits for child commands.

Ensures child process trees spawned by run_command / local_process cannot outlive
their parent (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) and are bounded by active process
count and optional memory limits. Gracefully degrades on non-Windows or nested jobs.
"""
from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


if os.name == "nt":
    from ctypes import wintypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class ProcessJob:
    """Managed Win32 Job Object; closing it terminates every process in the tree."""

    def __init__(self, max_processes: int = 32, max_memory_mb: int = 2048):
        self.handle = None
        self.active = False
        if os.name != "nt":
            return
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.restype = wintypes.BOOL
            self._k32 = k32

            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if max_processes > 0:
                flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                info.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
            if max_memory_mb > 0:
                flags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
                info.JobMemoryLimit = int(max_memory_mb) * 1024 * 1024
            info.BasicLimitInformation.LimitFlags = flags
            if not k32.SetInformationJobObject(
                h, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
            ):
                k32.CloseHandle(h)
                return
            self.handle = h
            self.active = True
        except Exception:
            self.handle = None
            self.active = False

    def assign(self, proc) -> bool:
        """Assign a subprocess.Popen instance to this Job Object."""
        if not self.handle or os.name != "nt":
            return False
        raw_handle = getattr(proc, "_handle", None)
        if not raw_handle:
            return False
        try:
            self._k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self._k32.AssignProcessToJobObject.restype = wintypes.BOOL
            return bool(self._k32.AssignProcessToJobObject(self.handle, int(raw_handle)))
        except Exception:
            return False

    def close(self) -> None:
        if self.handle and os.name == "nt":
            try:
                self._k32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None
            self.active = False


@contextmanager
def managed_job(max_processes: int = 32, max_memory_mb: int = 2048):
    job = ProcessJob(max_processes=max_processes, max_memory_mb=max_memory_mb)
    try:
        yield job
    finally:
        job.close()

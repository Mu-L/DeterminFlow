"""Process-tree ownership for one Workflow Executor generation."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100
PROCESS_SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INFINITE_PROBE_MS = 1000
RESUME_FAILED = 0xFFFFFFFF


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        if sys.platform.startswith("linux"):
            try:
                stat = Path(f"/proc/{pid}/stat").read_text(
                    encoding="utf-8", errors="replace",
                )
                if stat[stat.rfind(")") + 2:].split()[0] == "Z":
                    return False
            except (OSError, IndexError):
                pass
        return True
    api = _windows_api()
    handle = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = api.DWORD()
        if not api.GetExitCodeProcess(handle, api.byref(exit_code)):
            return False
        return int(exit_code.value) == STILL_ACTIVE
    finally:
        api.CloseHandle(handle)


def force_kill_pid(pid: int) -> None:
    """Terminate one process. Does not walk descendants."""
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def forced_exit_code() -> int:
    return signal.SIGTERM if os.name == "nt" else -signal.SIGKILL


class ExecutorProcessTree:
    """Own one Executor generation without touching Controller or siblings."""

    def __init__(self) -> None:
        self._pgid: int | None = None
        self._job: Any = None

    def spawn_options(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"start_new_session": True}
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_SUSPENDED
        return {"creationflags": flags}

    def attach(self, pid: int) -> None:
        if pid <= 0:
            return
        if os.name != "nt":
            self._pgid = pid
            return
        job = _WindowsJobObject.create()
        if job is None:
            raise RuntimeError("Workflow Executor Job Object creation failed")
        if not job.assign(pid):
            job.close()
            raise RuntimeError("Workflow Executor Job Object assignment failed")
        self._job = job
        try:
            _resume_process_threads(pid)
        except Exception:
            self._job = None
            job.terminate()
            job.close()
            raise

    def reap(self) -> None:
        if os.name != "nt":
            pgid = self._pgid
            self._pgid = None
            if pgid is None:
                return
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                return
            return
        job = self._job
        self._job = None
        if job is not None:
            job.terminate()
            job.close()

    def close(self) -> None:
        """Release the job handle. KILL_ON_JOB_CLOSE reaps leftovers."""
        self.reap()


async def watch_parent_exit(parent_pid: int, stop_event: asyncio.Event) -> None:
    """Set stop_event when the Controller parent is gone."""
    if os.name == "nt":
        await _watch_parent_windows(parent_pid, stop_event)
        return
    while not stop_event.is_set():
        if os.getppid() != parent_pid:
            logger.error("Controller parent exited; stopping Workflow Executor")
            stop_event.set()
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue


class _WindowsJobObject:
    def __init__(self, handle: Any):
        self._handle = handle

    @classmethod
    def create(cls) -> _WindowsJobObject | None:
        api = _windows_api()
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            logger.warning("CreateJobObjectW failed: %s", api.get_last_error())
            return None
        info = api.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            api.byref(info),
            api.sizeof(info),
        ):
            logger.warning(
                "SetInformationJobObject failed: %s", api.get_last_error()
            )
            api.CloseHandle(handle)
            return None
        return cls(handle)

    def assign(self, pid: int) -> bool:
        if self._handle is None:
            return False
        api = _windows_api()
        access = PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SYNCHRONIZE
        process = api.OpenProcess(access, False, pid)
        if not process:
            logger.warning(
                "OpenProcess for Job Object failed: %s", api.get_last_error()
            )
            return False
        try:
            if api.AssignProcessToJobObject(self._handle, process):
                return True
            logger.warning(
                "AssignProcessToJobObject failed: %s", api.get_last_error()
            )
            return False
        finally:
            api.CloseHandle(process)

    def terminate(self) -> None:
        if self._handle is None:
            return
        api = _windows_api()
        if not api.TerminateJobObject(self._handle, 1):
            logger.warning("TerminateJobObject failed: %s", api.get_last_error())

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle:
            _windows_api().CloseHandle(handle)


class _WindowsApi:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.byref = ctypes.byref
        self.sizeof = ctypes.sizeof
        self.DWORD = wintypes.DWORD
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32 = kernel32
        self.get_last_error = ctypes.get_last_error
        self.INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        self.JOBOBJECT_BASIC_LIMIT_INFORMATION = JOBOBJECT_BASIC_LIMIT_INFORMATION
        self.JOBOBJECT_EXTENDED_LIMIT_INFORMATION = (
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        )
        self.THREADENTRY32 = THREADENTRY32

        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD, wintypes.DWORD,
        ]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Thread32First.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(THREADENTRY32),
        ]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(THREADENTRY32),
        ]
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD

        self.CreateJobObjectW = kernel32.CreateJobObjectW
        self.SetInformationJobObject = kernel32.SetInformationJobObject
        self.AssignProcessToJobObject = kernel32.AssignProcessToJobObject
        self.TerminateJobObject = kernel32.TerminateJobObject
        self.OpenProcess = kernel32.OpenProcess
        self.GetExitCodeProcess = kernel32.GetExitCodeProcess
        self.WaitForSingleObject = kernel32.WaitForSingleObject
        self.CloseHandle = kernel32.CloseHandle
        self.CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
        self.Thread32First = kernel32.Thread32First
        self.Thread32Next = kernel32.Thread32Next
        self.OpenThread = kernel32.OpenThread
        self.ResumeThread = kernel32.ResumeThread


_WINDOWS_API: _WindowsApi | None = None


def _windows_api() -> _WindowsApi:
    global _WINDOWS_API
    if _WINDOWS_API is None:
        _WINDOWS_API = _WindowsApi()
    return _WINDOWS_API


def _resume_process_threads(pid: int) -> None:
    api = _windows_api()
    snapshot = api.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == api.INVALID_HANDLE_VALUE:
        raise RuntimeError("CreateToolhelp32Snapshot failed")
    try:
        entry = api.THREADENTRY32()
        entry.dwSize = api.sizeof(entry)
        more = api.Thread32First(snapshot, api.byref(entry))
        resumed = 0
        while more:
            if int(entry.th32OwnerProcessID) == pid:
                thread = api.OpenThread(
                    THREAD_SUSPEND_RESUME, False, int(entry.th32ThreadID),
                )
                if thread:
                    try:
                        previous = api.ResumeThread(thread)
                        if previous == RESUME_FAILED:
                            raise RuntimeError("ResumeThread failed")
                        resumed += 1
                    finally:
                        api.CloseHandle(thread)
            more = api.Thread32Next(snapshot, api.byref(entry))
        if resumed == 0:
            raise RuntimeError("Workflow Executor suspended thread was not found")
    finally:
        api.CloseHandle(snapshot)


async def _watch_parent_windows(parent_pid: int, stop_event: asyncio.Event) -> None:
    api = _windows_api()
    handle = api.OpenProcess(
        PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        parent_pid,
    )
    if not handle:
        logger.error("Controller parent handle is unavailable; stopping Workflow Executor")
        stop_event.set()
        return
    try:
        while not stop_event.is_set():
            result = await asyncio.to_thread(
                api.WaitForSingleObject, handle, INFINITE_PROBE_MS,
            )
            if result == WAIT_OBJECT_0:
                logger.error("Controller parent exited; stopping Workflow Executor")
                stop_event.set()
                return
            if result != WAIT_TIMEOUT:
                logger.error(
                    "Controller parent wait failed (%s); stopping Workflow Executor",
                    result,
                )
                stop_event.set()
                return
    finally:
        api.CloseHandle(handle)

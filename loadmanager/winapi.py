"""Windows-only calls that psutil does not provide: EcoQoS and working-set trimming.

Every function raises OSError when the OS refuses, so callers can skip one process
without aborting a whole session. On other platforms the module imports cleanly and
`available()` is False.
"""
from __future__ import annotations

import os

PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_POWER_THROTTLING = 4          # PROCESS_INFORMATION_CLASS
EXECUTION_SPEED = 0x1                 # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
CURRENT_VERSION = 1


def available() -> bool:
    return os.name == "nt"


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    for _name in ("GetProcessInformation", "SetProcessInformation"):
        _fn = getattr(_kernel32, _name, None)
        if _fn is not None:
            _fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            _fn.restype = wintypes.BOOL
    _kernel32.K32EmptyWorkingSet.argtypes = [ctypes.c_void_p]
    _kernel32.K32EmptyWorkingSet.restype = wintypes.BOOL

    class _Throttling(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    class _Handle:
        def __init__(self, pid: int, access: int):
            self.value = _kernel32.OpenProcess(access, False, pid)
            if not self.value:
                raise ctypes.WinError(ctypes.get_last_error())

        def __enter__(self):
            return self.value

        def __exit__(self, *_):
            _kernel32.CloseHandle(self.value)

    def get_eco(pid: int):
        """(ControlMask, StateMask) of the execution-speed throttle, or None if unsupported."""
        if not hasattr(_kernel32, "GetProcessInformation"):
            return None
        state = _Throttling(CURRENT_VERSION, 0, 0)  # Version is an input field even for Get
        with _Handle(pid, PROCESS_QUERY_LIMITED_INFORMATION) as handle:
            if not _kernel32.GetProcessInformation(handle, PROCESS_POWER_THROTTLING,
                                                   ctypes.byref(state), ctypes.sizeof(state)):
                raise ctypes.WinError(ctypes.get_last_error())
        return int(state.ControlMask), int(state.StateMask)

    def set_eco(pid: int, control: int, state: int) -> None:
        """Set the throttle masks exactly. (0, 0) returns the process to system control."""
        if not hasattr(_kernel32, "SetProcessInformation"):
            raise OSError("EcoQoS is not supported on this Windows version")
        value = _Throttling(CURRENT_VERSION, control, state)
        with _Handle(pid, PROCESS_SET_INFORMATION) as handle:
            if not _kernel32.SetProcessInformation(handle, PROCESS_POWER_THROTTLING,
                                                   ctypes.byref(value), ctypes.sizeof(value)):
                raise ctypes.WinError(ctypes.get_last_error())

    def trim(pid: int) -> None:
        """Move the process's pages to the standby list. Nothing is lost; pages fault back in on use."""
        with _Handle(pid, PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION) as handle:
            if not _kernel32.K32EmptyWorkingSet(handle):
                raise ctypes.WinError(ctypes.get_last_error())
    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

    class _SystemProcessInformation(ctypes.Structure):
        _fields_ = [("NextEntryOffset", ctypes.c_ulong), ("NumberOfThreads", ctypes.c_ulong),
                    ("WorkingSetPrivateSize", ctypes.c_longlong), ("HardFaultCount", ctypes.c_ulong),
                    ("NumberOfThreadsHighWatermark", ctypes.c_ulong), ("CycleTime", ctypes.c_ulonglong),
                    ("CreateTime", ctypes.c_longlong), ("UserTime", ctypes.c_longlong),
                    ("KernelTime", ctypes.c_longlong), ("ImageName", _UnicodeString),
                    ("BasePriority", ctypes.c_long), ("UniqueProcessId", ctypes.c_void_p),
                    ("InheritedFromUniqueProcessId", ctypes.c_void_p), ("HandleCount", ctypes.c_ulong),
                    ("SessionId", ctypes.c_ulong), ("UniqueProcessKey", ctypes.c_void_p),
                    ("PeakVirtualSize", ctypes.c_size_t), ("VirtualSize", ctypes.c_size_t),
                    ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t)]

    class _ReasonDetailed(ctypes.Structure):
        _fields_ = [("LocalizedReasonModule", ctypes.c_void_p), ("LocalizedReasonId", ctypes.c_ulong),
                    ("ReasonStringCount", ctypes.c_ulong), ("ReasonStrings", ctypes.c_void_p)]

    class _ReasonUnion(ctypes.Union):
        _fields_ = [("Detailed", _ReasonDetailed), ("SimpleReasonString", ctypes.c_wchar_p)]

    class _ReasonContext(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("Flags", ctypes.c_ulong), ("Reason", _ReasonUnion)]

    _kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
    _kernel32.PowerCreateRequest.restype = ctypes.c_void_p
    for _name in ("PowerSetRequest", "PowerClearRequest"):
        getattr(_kernel32, _name).argtypes = [ctypes.c_void_p, ctypes.c_int]
        getattr(_kernel32, _name).restype = wintypes.BOOL
    _POWER_REQUEST_SYSTEM_REQUIRED = 1
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class WakeRequest:
        """A Windows power request: while held, the laptop does not go to sleep on its own (the display
        may still turn off). It is cleared on release and by Windows when the process ends."""
        def __init__(self, reason="Load Manager: work is running in quiet mode"):
            self.reason, self.handle, self.held = reason, None, False

        def hold(self):
            if self.held:
                return
            if self.handle is None:
                context = _ReasonContext(0, 1)           # version 0, simple reason string
                context.Reason.SimpleReasonString = self.reason
                handle = _kernel32.PowerCreateRequest(ctypes.byref(context))
                if handle in (None, 0, _INVALID_HANDLE):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = handle
            if not _kernel32.PowerSetRequest(self.handle, _POWER_REQUEST_SYSTEM_REQUIRED):
                raise ctypes.WinError(ctypes.get_last_error())
            self.held = True

        def release(self):
            if self.held:
                _kernel32.PowerClearRequest(self.handle, _POWER_REQUEST_SYSTEM_REQUIRED)
                self.held = False

        def close(self):
            self.release()
            if self.handle is not None:
                _kernel32.CloseHandle(self.handle)
                self.handle = None

    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    _ntdll.NtQuerySystemInformation.restype = ctypes.c_long
    _EPOCH_DIFF = 116444736000000000      # 1601 to 1970 in 100 ns ticks
    _buffer_size = 1 << 20

    def process_table():
        """Every process in one system call: pid -> ppid, name, created (epoch seconds), cpu_seconds, rss.
        System services included, which per-process calls cannot read without administrator rights."""
        global _buffer_size
        for _ in range(6):
            buffer = ctypes.create_string_buffer(_buffer_size)
            needed = ctypes.c_ulong(0)
            status = _ntdll.NtQuerySystemInformation(5, buffer, _buffer_size, ctypes.byref(needed))
            if status == ctypes.c_long(0xC0000004).value:               # STATUS_INFO_LENGTH_MISMATCH
                _buffer_size = max(_buffer_size * 2, needed.value + (64 << 10))
                continue
            if status < 0:
                raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")
            break
        else:
            raise OSError("NtQuerySystemInformation kept asking for a larger buffer")
        table, offset, base = {}, 0, ctypes.addressof(buffer)
        while True:
            entry = _SystemProcessInformation.from_address(base + offset)
            pid = entry.UniqueProcessId or 0
            name = ""
            if entry.ImageName.Buffer and entry.ImageName.Length:
                name = ctypes.wstring_at(entry.ImageName.Buffer, entry.ImageName.Length // 2)
            elif pid == 0:
                name = "System Idle Process"
            table[pid] = {"ppid": entry.InheritedFromUniqueProcessId or 0, "name": name,
                          "created": (entry.CreateTime - _EPOCH_DIFF) / 1e7,
                          "cpu_seconds": (entry.UserTime + entry.KernelTime) / 1e7,
                          "rss": entry.WorkingSetSize}
            if not entry.NextEntryOffset:
                return table
            offset += entry.NextEntryOffset

    _powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
    _powrprof.SetSuspendState.argtypes = [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
    _powrprof.SetSuspendState.restype = wintypes.BOOLEAN

    def suspend() -> None:
        """Put the computer to sleep now (not to hibernate). Raises OSError when Windows refuses."""
        if not _powrprof.SetSuspendState(False, False, False):
            raise ctypes.WinError(ctypes.get_last_error())
else:
    def get_eco(pid: int):
        return None

    def suspend() -> None:
        raise OSError("Sleeping through this call is Windows-only")

    def set_eco(pid: int, control: int, state: int) -> None:
        raise OSError("EcoQoS is Windows-only")

    def trim(pid: int) -> None:
        raise OSError("Working-set trimming is Windows-only")

    def process_table():
        raise OSError("The bulk process table is Windows-only")

    class WakeRequest:
        held = False

        def __init__(self, reason=""):
            pass

        def hold(self):
            raise OSError("Power requests are Windows-only")

        def release(self):
            pass

        def close(self):
            pass

"""Memoria del ordenador: la libre (para no empezar algo que no cabe) y la total (undécima revisión)."""

from __future__ import annotations

import os
import sys


def total_memory() -> int | None:
    """Memoria física total en bytes (None si no se sabe)."""
    if sys.platform == "win32":
        st = _windows_status()
        return None if st is None else int(st.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return None


def available_memory() -> int | None:
    """Memoria física que se puede usar sin que el sistema empiece a quitar a otros (bytes; None si no se sabe).

    Linux: MemAvailable de /proc/meminfo. Windows: la física libre. macOS: no la da sin herramientas externas: la
    mitad de la total (una estimación prudente)."""
    if sys.platform == "win32":
        st = _windows_status()
        return None if st is None else int(st.ullAvailPhys)
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", encoding="ascii", errors="replace") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
    total = total_memory()
    return None if total is None else total // 2


def _windows_status():
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MemoryStatusEx()
    st.dwLength = ctypes.sizeof(MemoryStatusEx)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
            return None
    except (AttributeError, OSError):
        return None
    return st

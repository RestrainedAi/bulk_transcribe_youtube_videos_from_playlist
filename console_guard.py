"""DEV-175: keep a Windows console from freezing the watcher.

With QuickEdit on, one click (or a stray drag) in a console window selects text and
Windows pauses every write to that console until a key is pressed. A paused print()
blocks the whole watcher, which looks exactly like a hang that "clicking around" clears.
We turn QuickEdit off for our own console; it does nothing when there is no console.
"""
from __future__ import annotations

import os

ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
STD_INPUT_HANDLE = -10


def quickedit_off_mode(mode: int) -> int:
    """Console mode with QuickEdit cleared (EXTENDED_FLAGS must be set for it to stick)."""
    return (mode & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS


def disable_quickedit() -> bool:
    """Returns True if QuickEdit was turned off, False when there is no console or not Windows."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(STD_INPUT_HANDLE)
        mode = wintypes.DWORD()
        if not handle or not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(handle, quickedit_off_mode(mode.value)))
    except Exception:
        return False

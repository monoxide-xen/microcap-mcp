"""Keep Micro-Cap's windows out of the user's face while it runs.

Micro-Cap is a GUI application with no headless mode. In batch it still opens
its main window and plots each analysis as it goes — the manual says so
outright: *"the expressions specified in the circuit file would be plotted
during the run and be visible on the screen"*. Over a long sweep that means a
window appearing and stealing focus once per launch, which makes the machine
unusable for anything else.

``STARTUPINFO.wShowWindow = SW_HIDE`` does not work: it is only a hint for the
app's first ``ShowWindow`` call, and MC shows its windows explicitly. Measured
against MC 12.2.0.3, the window was visible in 9 of 10 samples with the flag
set.

The heavy fix is a separate window station/desktop, which means dropping
``subprocess`` for a raw ``CreateProcess``. This module does the cheap thing
instead: a watcher thread hides MC's windows as they appear, and foreground
locking stops them grabbing the caret mid-keystroke.

Caveats, honestly:

* The window can still flash for a frame or two before the watcher catches it.
  Polling faster narrows the gap; it cannot close it.
* Hiding may interfere with image export, which renders through the window.
  Check :func:`hidden` against your workload before trusting it.
"""

from __future__ import annotations

import ctypes
import os
import threading
from contextlib import contextmanager
from ctypes import wintypes

SW_HIDE = 0
SW_SHOWMINNOACTIVE = 7
LSFW_LOCK = 1
LSFW_UNLOCK = 2
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
# Far outside any plausible desktop, but still a legal window position: the
# window keeps painting, which is what image export needs.
OFFSCREEN = -32000

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # argtypes are not optional here. Without them ctypes marshals every
    # argument as a C int, which truncates a 64-bit HWND to 32 bits: the calls
    # then silently address nothing and the suppressor becomes a no-op that
    # still looks like it is working.
    _u32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
    _u32.EnumWindows.restype = wintypes.BOOL
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsWindowVisible.restype = wintypes.BOOL
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.ShowWindow.restype = wintypes.BOOL
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.GetWindowRect.restype = wintypes.BOOL
    _u32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _u32.SetWindowPos.restype = wintypes.BOOL
    _u32.LockSetForegroundWindow.argtypes = [wintypes.UINT]
    _u32.LockSetForegroundWindow.restype = wintypes.BOOL


def _windows_of(pid: int) -> list[int]:
    """Top-level window handles owned by one process."""
    found: list[int] = []

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append(hwnd)
        return True

    _u32.EnumWindows(_ENUM_PROC(cb), 0)
    return found


class WindowSuppressor:
    """Keeps a process's windows out of sight for as long as it lives.

    Args:
        pid: the process to police.
        interval: seconds between sweeps. Shorter means a briefer flash and
            more CPU; 0.03 is a reasonable trade.
        mode: how to get rid of the window.

            ``offscreen`` (default) moves it far outside the desktop. The
            window stays mapped, so Micro-Cap keeps painting it and image
            export still works.

            ``hide`` calls ``ShowWindow(SW_HIDE)``. Tidier and slightly
            faster, but an unmapped window paints nothing: Micro-Cap's plot
            export then yields a **fully black JPEG** — valid as a file,
            worthless as data. Only use it when exporting no images.

            ``minimise`` is a middle ground with the same rendering caveat.
    """

    def __init__(self, pid: int, interval: float = 0.03, mode: str = "offscreen"):
        if mode not in ("offscreen", "hide", "minimise"):
            raise ValueError(f"unknown mode {mode!r}")
        self.pid = pid
        self.interval = interval
        self.mode = mode
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.hidden_count = 0

    def _banish(self, hwnd: int) -> None:
        if self.mode == "offscreen":
            _u32.SetWindowPos(
                hwnd, HWND_BOTTOM, OFFSCREEN, OFFSCREEN, 0, 0,
                SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER,
            )
        else:
            _u32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE if self.mode == "minimise" else SW_HIDE)
        self.hidden_count += 1

    def _is_onscreen(self, hwnd: int) -> bool:
        r = wintypes.RECT()
        if not _u32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        return r.right > 0 and r.bottom > 0

    def _sweep(self) -> None:
        while not self._stop.is_set():
            for hwnd in _windows_of(self.pid):
                # Re-check every sweep rather than banishing once: Micro-Cap
                # restores its saved window placement after we move it, so a
                # one-shot approach leaves the window back on the desktop for
                # half the run.
                if self.mode == "offscreen":
                    if self._is_onscreen(hwnd):
                        self._banish(hwnd)
                elif _u32.IsWindowVisible(hwnd):
                    self._banish(hwnd)
            self._stop.wait(self.interval)

    def start(self) -> "WindowSuppressor":
        if not _IS_WINDOWS:
            return self
        self._thread = threading.Thread(target=self._sweep, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


@contextmanager
def foreground_locked():
    """Stop any app grabbing the foreground while this block runs.

    This is what actually protects the user's typing: without it Micro-Cap
    pulls the caret away mid-keystroke every time it opens a window.
    """
    if not _IS_WINDOWS:
        yield
        return
    _u32.LockSetForegroundWindow(LSFW_LOCK)
    try:
        yield
    finally:
        _u32.LockSetForegroundWindow(LSFW_UNLOCK)


@contextmanager
def hidden(pid: int, interval: float = 0.03, mode: str = "offscreen"):
    """Suppress a process's windows and hold the foreground for the duration."""
    sup = WindowSuppressor(pid, interval=interval, mode=mode).start()
    try:
        with foreground_locked():
            yield sup
    finally:
        sup.stop()

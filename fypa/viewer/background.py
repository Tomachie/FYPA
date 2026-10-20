# Copyright (c) 2026 Tomachie LLC.
"""A start in the background (``FYPA gui --background``): for a run started by a script
while the user works in another program.

Every FYPA window opens minimized on the taskbar without taking the focus, the import's
progress dialog is not shown, and a dialog the import would raise (a failure, a warning
about the design) waits until the user opens the window, then shows over it. Once the
user has opened a window, FYPA behaves as it always does.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt

# Windows ShowWindow: minimized, without activating (the window that had the focus keeps it).
SW_SHOWMINNOACTIVE = 7

_enabled = False


def enable() -> None:
    global _enabled
    _enabled = True


def active(win=None) -> bool:
    """Started in the background and not opened by the user yet (``win``, if given,
    still minimized or not shown)."""
    if not _enabled:
        return False
    return win is None or win.isMinimized() or not win.isVisible()


def show(win) -> None:
    """Show ``win``: minimized without activating while :func:`active`, else as usual."""
    if not _enabled:
        win.show()
        return
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    if sys.platform == "win32":
        # Qt shows a window that does not activate as a normal one whatever its state;
        # Windows minimizes it without activating it.
        win.show()
        import ctypes
        ctypes.windll.user32.ShowWindow(int(win.winId()), SW_SHOWMINNOACTIVE)
    else:
        win.setWindowState(Qt.WindowState.WindowMinimized)
        win.show()


class _WhenOpened(QObject):
    """Runs the calls once ``win`` is restored from the taskbar."""

    def __init__(self, win, calls: list[Callable[[], None]]):
        super().__init__(win)
        self.win, self.calls = win, calls
        win.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if (obj is self.win and event.type() == QEvent.Type.WindowStateChange
                and not self.win.isMinimized()):
            self.win.removeEventFilter(self)
            calls, self.calls = self.calls, []
            for call in calls:
                call()
        return False


def when_opened(win, *calls: Callable[[], None]) -> None:
    """Run ``calls`` now, or - while ``win`` is still minimized from a background start -
    once the user opens it."""
    if active(win):
        _WhenOpened(win, list(calls))
    else:
        for call in calls:
            call()

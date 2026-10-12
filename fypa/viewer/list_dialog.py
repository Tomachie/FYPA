"""A notice with a list that can be long: the list scrolls inside a dialog of sensible
size instead of growing the dialog past the screen (a board can have dozens of
skipped directives or unannotated bridges)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QStyle, QVBoxLayout,
)

MAX_SCREEN_FRACTION = 0.6
WIDTH_PX = 760


def show_list_notice(parent, title: str, intro: str, items: list[str], outro: str = "",
                     warning: bool = False) -> None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    outer = QVBoxLayout(dlg)

    head = QHBoxLayout()
    icon = QLabel()
    pix = QStyle.SP_MessageBoxWarning if warning else QStyle.SP_MessageBoxInformation
    icon.setPixmap(dlg.style().standardIcon(pix).pixmap(40, 40))
    head.addWidget(icon, 0)
    text = QLabel(intro)
    text.setWordWrap(True)
    head.addWidget(text, 1)
    outer.addLayout(head)

    lst = QListWidget()
    lst.setWordWrap(True)
    lst.setAlternatingRowColors(True)
    for item in items:
        lst.addItem(QListWidgetItem(item))
    outer.addWidget(lst, 1)

    if outro:
        tail = QLabel(outro)
        tail.setWordWrap(True)
        outer.addWidget(tail)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    outer.addWidget(buttons)

    screen = (parent.screen() if parent is not None else None) or QApplication.primaryScreen()
    max_h = int(screen.availableGeometry().height() * MAX_SCREEN_FRACTION) if screen else 600
    dlg.resize(WIDTH_PX, min(max_h, 220 + 60 * len(items)))
    dlg.setMaximumHeight(max_h if max_h > 300 else 600)
    dlg.exec()


__all__ = ["show_list_notice"]

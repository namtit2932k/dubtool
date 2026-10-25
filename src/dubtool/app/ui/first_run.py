"""First-run privacy notice (AGU4 9.2): shown once before the first run,
re-readable from About. Accurate about what leaves the computer; never more,
never less.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dubtool.app import i18n, settings


class PrivacyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("first_run.privacy").split(":")[0])
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        text = QLabel(i18n.tr("first_run.privacy"))
        text.setWordWrap(True)
        inner_layout.addWidget(text)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        self.ack = QCheckBox(i18n.tr("first_run.understand"))
        layout.addWidget(self.ack)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.continue_btn = QPushButton(i18n.tr("first_run.continue"))
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.continue_btn)
        layout.addLayout(btn_row)
        self.ack.toggled.connect(self.continue_btn.setEnabled)


def acknowledged() -> bool:
    return bool(settings.load().get("privacy_acknowledged"))


def acknowledge() -> None:
    s = settings.load()
    s["privacy_acknowledged"] = True
    settings.save(s)


def ensure_shown(parent=None) -> bool:
    """Show once; returns True when acknowledged (including earlier runs)."""
    if acknowledged():
        return True
    dialog = PrivacyDialog(parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        acknowledge()
        return True
    return False

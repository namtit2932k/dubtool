"""The one confirmation before a first paid-provider run (Update 13).

States plainly: the model this run uses bills per token on the user's own
{provider} account, and the video's English text is sent to that provider.
The checkbox ("Không hỏi lại") remembers the choice per provider in
settings.json (paid_notice_ok) so the confirmation appears once per
provider. Nothing here is a data-retention claim — the retention note
exists one place only: the first-run privacy notice ("xem điều khoản của
nhà cung cấp").
"""

from __future__ import annotations

from dubtool.app import i18n

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class PaidProviderConfirmDialog(QDialog):
    """Accept = run (QDialog.Accepted), Cancel/Esc/Close = abort."""

    def __init__(self, parent=None, provider_label: str = "OpenAI"):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("paid.notice_title"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        text = QLabel(i18n.tr("paid.notice_text", provider=provider_label))
        text.setWordWrap(True)
        layout.addWidget(text)
        retention = QLabel(i18n.tr("paid.retention_note"))
        retention.setWordWrap(True)
        layout.addWidget(retention)
        self.dont_ask = QCheckBox(i18n.tr("paid.dont_ask"))
        layout.addWidget(self.dont_ask)
        row = QHBoxLayout()
        row.addStretch(1)
        run_btn = QPushButton(i18n.tr("paid.run_once"))
        run_btn.setDefault(True)
        cancel_btn = QPushButton(i18n.tr("progress.cancel"))
        run_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(run_btn)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

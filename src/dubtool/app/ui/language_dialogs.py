"""Dialogs for source-language selection, uncertain detection, and Vietnamese source notice."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from dubtool import languages as lang_mod
from dubtool.app import i18n


class OtherLanguageDialog(QDialog):
    """Searchable list of every language supported by faster-whisper."""

    def __init__(self, parent=None, ui_lang: str | None = None):
        super().__init__(parent)
        self._ui_lang = ui_lang or ("en" if i18n.language() == "en" else "vi")
        self.selected_code: str | None = None
        self.setWindowTitle(i18n.tr("language.other_title"))
        self.setMinimumWidth(420)
        self.setMinimumHeight(440)

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n.tr("language.other_search"))
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        self._all_items: list[tuple[str, str]] = []
        for code in sorted(lang_mod.supported_language_codes()):
            label = f"{lang_mod.dropdown_label(code, self._ui_lang)} [{code}]"
            self._all_items.append((code, label))
        self._populate("")

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton(i18n.tr("language.cancel"))
        self.ok_btn = QPushButton(i18n.tr("language.ok"))
        self.ok_btn.setDefault(True)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        self.search.textChanged.connect(self._populate)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._accept_current())
        self.ok_btn.clicked.connect(self._accept_current)
        self.cancel_btn.clicked.connect(self.reject)

    def _populate(self, query: str) -> None:
        q = (query or "").strip().lower()
        self.list_widget.clear()
        for code, label in self._all_items:
            if not q or q in label.lower() or q in code.lower():
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, code)
                self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        self.selected_code = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if self.selected_code:
            self.accept()


class LanguageUncertainDialog(QDialog):
    """Shown in GUI when auto-detection confidence is < 0.60 or two languages
    each occupy >= 25% of speech windows. Presents the top 3 candidates."""

    def __init__(
        self,
        candidates: list[dict],
        *,
        mixed: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.choice: str | None = None
        self._candidates = list(candidates[:3])
        self.setWindowTitle(i18n.tr("language.uncertain_title"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        body_key = "language.uncertain_mixed_body" if mixed else "language.uncertain_body"
        lbl = QLabel(i18n.tr(body_key))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.group = QButtonGroup(self)
        self.radios: list[tuple[QRadioButton, str]] = []
        ui_lang = "en" if i18n.language() == "en" else "vi"
        for idx, cand in enumerate(self._candidates):
            code = str(cand.get("code") or "en").lower()
            pct = int(round(float(cand.get("probability") or 0.0) * 100))
            name = lang_mod.dropdown_label(code, ui_lang)
            rb = QRadioButton(f"{name} — {pct}%")
            if idx == 0:
                rb.setChecked(True)
            self.group.addButton(rb, idx)
            self.radios.append((rb, code))
            layout.addWidget(rb)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton(i18n.tr("language.cancel"))
        self.continue_btn = QPushButton(i18n.tr("language.uncertain_continue"))
        self.continue_btn.setDefault(True)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.continue_btn)
        layout.addLayout(btn_row)

        self.continue_btn.clicked.connect(self._on_continue)
        self.cancel_btn.clicked.connect(self.reject)

    def _on_continue(self) -> None:
        for rb, code in self.radios:
            if rb.isChecked():
                self.choice = code
                break
        self.accept()


class VietnameseSourceDialog(QDialog):
    """Shown when the input video is detected as already Vietnamese: stops
    before translation/TTS and offers subtitles (.srt) & transcript export."""

    def __init__(self, probability: float, parent=None):
        super().__init__(parent)
        self.choice: str = "close"
        pct = int(round(float(probability) * 100))
        self.setWindowTitle(i18n.tr("language.vi_title"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        msg = QLabel(i18n.tr("language.vi_body", pct=pct))
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.subtitles_btn = QPushButton(i18n.tr("language.vi_subtitles_btn"))
        self.subtitles_btn.setDefault(True)
        self.close_btn = QPushButton(i18n.tr("language.close"))
        btn_row.addWidget(self.subtitles_btn)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self.subtitles_btn.clicked.connect(self._on_subtitles)
        self.close_btn.clicked.connect(self.reject)

    def _on_subtitles(self) -> None:
        self.choice = "subtitles"
        self.accept()

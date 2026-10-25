"""Settings dialog (AGU4 8): Gemini key (never shown again after saving),
language, Whisper model/device, Advanced model overrides.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from dubtool.app import i18n, modes, secrets, settings, selftest

VOICE_KEYS = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")


class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_key_saved=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("app.settings"))
        self.setMinimumWidth(560)
        self._on_key_saved = on_key_saved

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # --- API key -------------------------------------------------------
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_show = QCheckBox()
        key_row.addWidget(self.key_edit, 1)
        self.key_save_btn = QPushButton(i18n.tr("settings.save"))
        self.key_test_btn = QPushButton(i18n.tr("settings.test"))
        self.key_delete_btn = QPushButton(i18n.tr("settings.delete"))
        key_row.addWidget(self.key_save_btn)
        key_row.addWidget(self.key_test_btn)
        key_row.addWidget(self.key_delete_btn)
        form.addRow(i18n.tr("settings.key"), key_row)
        self.key_hint = QLabel(i18n.tr("settings.key.hint"))
        self.key_hint.setWordWrap(True)
        form.addRow("", self.key_hint)
        self.key_status = QLabel("")
        form.addRow("", self.key_status)

        # --- Language / Whisper --------------------------------------------
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Tiếng Việt", "vi")
        self.lang_combo.addItem("English", "en")
        form.addRow(i18n.tr("settings.language"), self.lang_combo)

        self.voice_combo = QComboBox()
        for voice in VOICE_KEYS:
            self.voice_combo.addItem(voice, voice)
        form.addRow(i18n.tr("options.voice"), self.voice_combo)

        self.whisper_combo = QComboBox()
        for size in ("tiny", "base", "small", "medium", "large-v3"):
            self.whisper_combo.addItem(size, size)
        form.addRow(i18n.tr("settings.whisper_model"), self.whisper_combo)

        self.device_combo = QComboBox()
        for dev in ("auto", "cpu", "cuda"):
            self.device_combo.addItem(dev, dev)
        form.addRow(i18n.tr("settings.whisper_device"), self.device_combo)

        # --- Advanced --------------------------------------------------------
        self.model_fast_edit = QLineEdit()
        form.addRow(i18n.tr("settings.model_fast"), self.model_fast_edit)
        self.model_accurate_edit = QLineEdit()
        form.addRow(i18n.tr("settings.model_accurate"), self.model_accurate_edit)
        self.reset_btn = QPushButton(i18n.tr("settings.reset_models"))
        form.addRow("", self.reset_btn)
        self.jobs_edit = QLineEdit()
        form.addRow(i18n.tr("settings.jobs_dir"), self.jobs_edit)
        self.output_edit = QLineEdit()
        form.addRow(i18n.tr("settings.default_output"), self.output_edit)

        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close = QPushButton("OK")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        layout.addLayout(btn_row)

        # behavior
        self.key_show.setTristate(False)
        self.key_show.setText("👁")
        self.key_show.toggled.connect(self._toggle_key_echo)
        self.key_save_btn.clicked.connect(self._save_key)
        self.key_test_btn.clicked.connect(self._test_key)
        self.key_delete_btn.clicked.connect(self._delete_key)
        self.reset_btn.clicked.connect(self._reset_models)
        self._load()

    def _load(self) -> None:
        from dubtool.app.secrets import key_source

        source = key_source()
        self.key_status.setText(
            i18n.tr("settings.key_saved") if source == "keyring"
            else i18n.tr("mode.no_key") if source == "none"
            else ""
        )
        s = settings.load()
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(s["language"])))
        self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(s["voice"])))
        self.whisper_combo.setCurrentIndex(max(0, self.whisper_combo.findData(s["whisper_model"])))
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(s["whisper_device"])))
        self.jobs_edit.setText(s.get("jobs_dir") or "")
        self.output_edit.setText(s.get("default_output") or "")
        current = modes.load(settings.load())
        self.model_fast_edit.setText(current["fast"]["model"])
        self.model_accurate_edit.setText(current["accurate"]["model"])

    def _toggle_key_echo(self, shown: bool) -> None:
        self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        )

    def _save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_status.setText(i18n.tr("mode.no_key"))
            return
        try:
            secrets.save_key(key)
        except Exception as exc:
            self.key_status.setText(str(exc))
            return
        self.key_edit.clear()
        # show only the last 4 characters, never the key itself (AGU4 8)
        self.key_status.setText(i18n.tr("settings.key_saved") + f" (…{key[-4:]})")
        if self._on_key_saved:
            self._on_key_saved()

    def _test_key(self) -> None:
        key = self.key_edit.text().strip() or secrets.get_gemini_key()
        if not key:
            self.key_status.setText(i18n.tr("mode.no_key"))
            return
        try:
            from dubtool.app.key_test import run_key_test

            outcome = run_key_test(key)
            text = {
                "ok": i18n.tr("settings.key_ok"),
                "invalid": i18n.tr("settings.key_invalid"),
                "network": i18n.tr("settings.network"),
                "quota": i18n.tr("settings.quota"),
            }.get(outcome, i18n.tr("settings.error_unk"))
            self.key_status.setText(text)
        except Exception as exc:
            self.key_status.setText(i18n.tr("settings.error_unk"))
            self._error_detail = str(exc)

    def _delete_key(self) -> None:
        secrets.delete_key()
        self.key_status.setText(i18n.tr("settings.key_removed"))

    def _reset_models(self) -> None:
        modes.reset_to_defaults()
        current = modes.load(settings.load())
        self.model_fast_edit.setText(current["fast"]["model"])
        self.model_accurate_edit.setText(current["accurate"]["model"])

    def apply_changes(self) -> dict:
        s = settings.load()
        s["language"] = self.lang_combo.currentData() or "vi"
        s["voice"] = self.voice_combo.currentData() or s["voice"]
        s["whisper_model"] = self.whisper_combo.currentData() or s["whisper_model"]
        s["whisper_device"] = self.device_combo.currentData() or s["whisper_device"]
        s["jobs_dir"] = self.jobs_edit.text().strip()
        s["default_output"] = self.output_edit.text().strip()
        s["mode_models"] = {
            "fast": self.model_fast_edit.text().strip(),
            "accurate": self.model_accurate_edit.text().strip(),
        }
        problems = settings.validate(s)
        if problems:
            QMessageBox.warning(self, i18n.tr("app.settings"), "\n".join(problems))
            return None  # type: ignore[return-value]
        settings.save(s)
        i18n.set_language(s["language"])
        return s

    def accept(self) -> None:  # noqa: D102
        if self.apply_changes() is not None:
            super().accept()

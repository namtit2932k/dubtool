"""Slot-exception guard (AGENT_UPDATE_7.md 2.3).

Exceptions raised inside Qt slots are otherwise swallowed to a stderr a
windowed exe does not have — the user sees nothing. `install()` hooks
`sys.excepthook` and Qt's message handler, writes the traceback (with any
key-like value redacted) to %LOCALAPPDATA%/DubTool/logs/, and shows one
non-technical dialog per distinct error with "Copy details" and
"Export diagnostics".
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from pathlib import Path

log = logging.getLogger("dubtool")

_REPORTED: set[str] = set()  # distinct-error keys -> shown-once tracking
_DIALOG_OPEN = False         # never stack two dialogs

# Lines our Qt message handler recognizes as routed slot failures.
_QT_SLOT_LOG_PREFIX = "unhandled exception in slot"

# Key-shaped tokens (Google AIZA…, Anthropic sk-ant-…, Azure-style "AQ.…") —
# AGENT_UPDATE_7.md rule 2: a key value is NEVER printed, even in a log or
# "details" box. Positions/last-4 are the only allowed representation.
_KEY_SHAPED = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"AQ\.[0-9A-Za-z_\-]{20,}"),
)


def scrub(text: str) -> str:
    """Redact key-shaped tokens from diagnostic text."""
    out = text
    for pattern in _KEY_SHAPED:
        out = pattern.sub("<redacted>", out)
    return out


def describe(exc: BaseException) -> str:
    """Full diagnostic text with key values redacted (never printed raw)."""
    text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return scrub(text)


def _write_log(text: str) -> Path | None:
    """Append to a numbered log file; returns the path (None unwritable)."""
    try:
        from dubtool.app.paths import logs_dir

        d = logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        names = sorted(d.glob("slot-error-*.log"))
        # Keep at most 5 old files (AGU6 housekeeping spirit).
        for old in names[:-5]:
            old.unlink(missing_ok=True)
        path = d / f"slot-error-{len(names) + 1:03d}.log"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        return None


def _distinct_key(text: str) -> str:
    """Traceback content minus paths/line numbers: one dialog per distinct
    failure, never per occurrence."""
    collapsed = re.sub(r'File "[^"]+", line \d+', 'File "<loc>"', text)
    return collapsed


def handle_slot_exception(exc: BaseException) -> None:
    """Log, then show the dialog once per distinct error."""
    text = f"{_QT_SLOT_LOG_PREFIX}\n{describe(exc)}"
    log.exception("unhandled exception in slot")
    _write_log(text)
    key = _distinct_key(text)
    if key in _REPORTED:
        return
    _REPORTED.add(key)
    _show_dialog(exc)


def _show_dialog(exc: BaseException) -> None:
    """One non-technical dialog per distinct error, with copy/export."""
    global _DIALOG_OPEN
    if _DIALOG_OPEN:
        return
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:  # no UI yet: the log is enough
            return
        from dubtool.app import i18n
        from dubtool.app.errors import message_for_code

        import hashlib

        text = describe(exc)
        reference = hashlib.sha1(_distinct_key(text).encode()).hexdigest()[:8]

        _DIALOG_OPEN = True
        try:
            dialog = QMessageBox()
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle("DubTool")
            dialog.setText(message_for_code("error"))
            dialog.setInformativeText(i18n.tr("error.internal"))
            dialog.setDetailedText(f"reference: {reference}\n{text}")

            copy_btn = dialog.addButton(
                i18n.tr("copy_details"), QMessageBox.ButtonRole.ActionRole
            )
            copy_btn.clicked.connect(
                lambda: QApplication.clipboard().setText(text)
            )
            export_btn = dialog.addButton(
                i18n.tr("app.export_diagnostics"),
                QMessageBox.ButtonRole.ActionRole,
            )

            def _export() -> None:
                try:
                    from dubtool.app import diagnostics

                    diagnostics.export_diagnostics(
                        Path.home() / "Desktop" / "dubtool-diagnostics.zip"
                    )
                    log.info("diagnostics exported from failguard dialog")
                except Exception:
                    log.exception("diagnostics export failed")

            export_btn.clicked.connect(_export)
            dialog.addButton(QMessageBox.StandardButton.Ok)
            dialog.exec()
        finally:
            _DIALOG_OPEN = False
    except Exception:  # the guard itself must never crash the app
        log.exception("failguard dialog failed")


def install() -> None:
    """Install sys.excepthook + a Qt message handler (idempotent)."""
    if getattr(sys, "_dubtool_failguard", False):
        return
    sys._dubtool_failguard = True  # type: ignore[attr-defined]

    def _excepthook(kind, value, tb):  # noqa: ANN001
        if kind is SystemExit:
            sys.__excepthook__(kind, value, tb)
            return
        try:
            exc = value if isinstance(value, BaseException) else kind(value)
            handle_slot_exception(exc)
        except Exception:
            log.exception("failguard excepthook failed")

    sys.excepthook = _excepthook

    try:
        from PySide6.QtCore import qInstallMessageHandler

        def _qt_handler(mode, context, message) -> None:  # noqa: ANN001
            if _QT_SLOT_LOG_PREFIX in message:
                try:
                    handle_slot_exception(RuntimeError(message))
                except Exception:
                    log.exception("failguard Qt handler failed")
            sys.stderr.write(f"Qt: {message}\n")

        qInstallMessageHandler(_qt_handler)
    except Exception:  # Qt not present (headless unit runs)
        pass

"""Offline Vietnamese TTS provider backed by VieNeu-TTS v3 ONNX (CPU).

Supports two ONNX/CPU modes:
  - ``v3turbo_int8`` (default): 48 kHz, INT8 quantized backbone (~244 MB download, ~2.2 GB RAM)
  - ``v3nano``: 24 kHz, flow-matching ONNX model (~269 MB download, ~480 MB RAM)

Models are downloaded on demand from Hugging Face into ``<models_dir>/hf_cache``
(never bundled in the application zip). A preset voice is ALWAYS passed
explicitly (default ``"Trúc Ly"``) so VieNeu's internal default is never used
unintentionally.
"""

from __future__ import annotations

from contextlib import contextmanager
import gc
import io
import json
import logging
import os
from pathlib import Path
import sys
import threading
import uuid
from typing import Any

import numpy as np

from dubtool.tts.base import TTSEngineInitError, TTSFailure, TTSProvider

log = logging.getLogger("dubtool")

DEFAULT_TTS_MODE = "v3turbo_int8"
DEFAULT_VOICE = "Trúc Ly"

# Mode definitions mapped to Vieneu(...) factory kwargs and audio properties.
TTS_MODES: dict[str, dict[str, Any]] = {
    "v3turbo_int8": {
        "vieneu_mode": "v3turbo",
        "backend": "onnx",
        "precision": "int8",
        "sample_rate": 48_000,
        "max_chars": 256,
        "assets_file": "voices_v3_turbo.json",
        "repos": (
            "pnnbao-ump/VieNeu-TTS-v3-Turbo",
            "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX",
        ),
        "download_mb": 244,
    },
    "v3nano": {
        "vieneu_mode": "v3nano",
        "backend": "onnx",
        "precision": "fp32",
        "steps": 16,
        "sample_rate": 24_000,
        "max_chars": 140,
        "assets_file": "voices_v3_nano.json",
        "repos": ("pnnbao-ump/VieNeu-TTS-v3-Nano",),
        "download_mb": 269,
    },
}

# Voices available across BOTH v3turbo_int8 and v3nano, pinned at the top of UI lists.
SHARED_VOICES: tuple[str, ...] = (
    "Trúc Ly",
    "Mai Anh",
    "Mỹ Duyên",
    "Đức Trí",
    "Adam",
)

# Legacy edge-tts voice IDs migrated automatically on settings/config load.
LEGACY_VOICE_MAP: dict[str, str] = {
    "vi-VN-HoaiMyNeural": "Trúc Ly",
    "vi-VN-NamMinhNeural": "Đức Trí",
}

# Fallback static catalogs in case vieneu assets JSON cannot be read (e.g. mocked tests).
_FALLBACK_TURBO_VOICES: tuple[tuple[str, str, str], ...] = (
    ("Trúc Ly", "female", "Nữ · Bắc · Phong cách tự nhiên"),
    ("Mai Anh", "female", "Nữ · Bắc · Phong cách tin tức"),
    ("Mỹ Duyên", "female", "Nữ · Nam · Phong cách đọc truyện"),
    ("Đức Trí", "male", "Nam · Nam · Phong cách đọc truyện"),
    ("Adam", "male", "Nam · Nam · Giọng đọc tự nhiên"),
    ("Hải Đăng", "male", "Nam · Bắc · Phong cách tự nhiên"),
    ("Thùy Dung", "female", "Nữ · Nam · Phong cách tin tức"),
    ("Ngọc Huyền", "female", "Nữ · Bắc · Giọng đọc tự nhiên"),
    ("Quang Sơn", "male", "Nam · Trung · Phong cách tự nhiên"),
    ("Ngọc Trân", "female", "Nữ · Trung · Phong cách tự nhiên"),
    ("Thiện Minh", "male", "Nam · Bắc · Phong cách kể chuyện"),
    ("Thiền Tâm Đức", "male", "Nam · Bắc · Phong cách kể chuyện"),
    ("Minh Đức", "male", "Nam · Bắc · Phong cách tin tức"),
    ("Phạm Tuyên", "male", "Nam · Bắc · Phong cách tự nhiên"),
    ("Thái Sơn", "male", "Nam · Nam · Phong cách kể chuyện"),
    ("Xuân Vĩnh", "male", "Nam · Bắc · Phong cách tự nhiên"),
    ("Thanh Bình", "male", "Nam · Bắc · Phong cách kể chuyện"),
    ("Ngọc Linh", "female", "Nữ · Bắc · Phong cách kể chuyện"),
    ("Đoan Trang", "female", "Nữ · Bắc · Phong cách tự nhiên"),
    ("Thục Đoan", "female", "Nữ · Nam · Phong cách kể chuyện"),
    ("Minh Triết", "male", "Nam · Nam · Phong cách tin tức"),
    ("Quỳnh Anh", "female", "Nữ · Bắc · Phong cách đọc truyện"),
    ("Kim Thanh", "female", "Nữ · Nam · Phong cách đọc truyện"),
    ("Quốc Tuấn", "male", "Nam · Bắc · Phong cách tự nhiên"),
    ("Adam bựa", "male", "Nam · Bắc · Phong cách tự nhiên"),
)

_FALLBACK_NANO_VOICES: tuple[tuple[str, str, str], ...] = (
    ("Trúc Ly", "female", "Nữ · Bắc · Phong cách tự nhiên"),
    ("Mai Anh", "female", "Nữ · Bắc · Phong cách tin tức"),
    ("Mỹ Duyên", "female", "Nữ · Bắc · Phong cách đọc truyện"),
    ("Đức Trí", "male", "Nam · Bắc · Phong cách đọc truyện"),
    ("Adam", "male", "Nam · Nam · Giọng đọc tự nhiên"),
    ("Minh Quân", "male", "Nam · Bắc · Phong cách tự nhiên"),
    ("Ái Hân", "female", "Nữ · Nam · Phong cách tin tức"),
    ("Hữu Quân", "male", "Nam · Bắc · Phong cách tin tức"),
    ("Xuân Tiên", "female", "Nữ · Bắc · Phong cách tin tức"),
    ("Anh Khôi", "male", "Nam · Bắc · Phong cách kể chuyện"),
    ("Mạnh Dũng", "male", "Nam · Bắc · Phong cách tự nhiên"),
)

_CATALOG_CACHE: dict[str, list[dict[str, str]]] = {}


def normalize_mode(mode: str | None) -> str:
    """Return a valid TTS mode key, defaulting to ``v3turbo_int8``."""
    raw = str(mode or "").strip().lower()
    if raw in TTS_MODES:
        return raw
    if raw in ("v3turbo", "turbo", "turbo_int8"):
        return "v3turbo_int8"
    if raw in ("nano", "v3_nano"):
        return "v3nano"
    return DEFAULT_TTS_MODE


def mode_sample_rate(mode: str | None) -> int:
    return int(TTS_MODES[normalize_mode(mode)]["sample_rate"])


def migrate_voice(voice: str | None) -> str:
    """Map legacy edge-tts voice IDs to VieNeu preset names."""
    v = str(voice or "").strip()
    if not v:
        return DEFAULT_VOICE
    return LEGACY_VOICE_MAP.get(v, v)


def _vieneu_assets_dir() -> Path | None:
    try:
        import vieneu

        p = Path(vieneu.__file__).resolve().parent / "assets"
        if p.is_dir():
            return p
    except Exception:
        pass
    return None


def list_preset_voices(mode: str | None = None) -> list[dict[str, str]]:
    """Return preset voice metadata for ``mode`` with SHARED_VOICES pinned first.

    Each item is ``{"id": name, "label": display_label, "gender": ..., "description": ...}``.
    Reads ``vieneu/assets/voices_v3_*.json`` without loading any ONNX weights.
    """
    m = normalize_mode(mode)
    if m in _CATALOG_CACHE:
        return list(_CATALOG_CACHE[m])

    raw_items: list[tuple[str, str, str, int | None]] = []
    assets = _vieneu_assets_dir()
    assets_file = TTS_MODES[m]["assets_file"]
    if assets is not None and (assets / assets_file).is_file():
        try:
            data = json.loads((assets / assets_file).read_text(encoding="utf-8"))
            for name, meta in (data.get("presets") or {}).items():
                desc = str(meta.get("description") or "").strip()
                gender = str(meta.get("gender") or "").strip()
                feat = meta.get("featured")
                try:
                    feat_rank = int(feat) if feat is not None else None
                except (TypeError, ValueError):
                    feat_rank = None
                raw_items.append((str(name), gender, desc, feat_rank))
        except Exception as exc:
            log.debug("vieneu: failed to read %s (%s), using fallback catalog", assets_file, exc)

    if not raw_items:
        fallback = _FALLBACK_TURBO_VOICES if m == "v3turbo_int8" else _FALLBACK_NANO_VOICES
        raw_items = [(n, g, d, None) for n, g, d in fallback]

    shared_order = {name: idx for idx, name in enumerate(SHARED_VOICES)}

    def _sort_key(item: tuple[str, str, str, int | None]) -> tuple[int, int, int, str]:
        name, _g, _d, feat = item
        if name in shared_order:
            return (0, shared_order[name], 0, name)
        if feat is not None:
            return (1, feat, 0, name)
        return (2, 999, 0, name)

    raw_items.sort(key=_sort_key)
    result: list[dict[str, str]] = []
    for name, gender, desc, _feat in raw_items:
        label = f"{name} — {desc}" if desc else name
        result.append({
            "id": name,
            "label": label,
            "gender": gender,
            "description": desc,
        })
    _CATALOG_CACHE[m] = result
    return list(result)


def valid_voice_ids(mode: str | None = None) -> tuple[str, ...]:
    return tuple(v["id"] for v in list_preset_voices(mode))


def resolve_voice(voice: str | None, mode: str | None = None) -> str:
    """Resolve a configured/requested voice to a valid preset for ``mode``.

    Migrates legacy edge-tts IDs first, then falls back to ``DEFAULT_VOICE``
    (``"Trúc Ly"``) if the voice is unavailable in the chosen mode.
    """
    m = normalize_mode(mode)
    migrated = migrate_voice(voice)
    ids = set(valid_voice_ids(m))
    if migrated in ids:
        return migrated
    log.warning(
        "vieneu: voice %r is not available in mode %s — falling back to %r",
        voice, m, DEFAULT_VOICE,
    )
    return DEFAULT_VOICE if DEFAULT_VOICE in ids else next(iter(ids), DEFAULT_VOICE)


def hf_cache_dir() -> Path:
    """Directory where VieNeu Hugging Face ONNX weights are cached."""
    override = os.environ.get("DUBTOOL_VIENEU_CACHE", "").strip()
    if override:
        p = Path(override)
    else:
        from dubtool.app import paths

        p = paths.models_dir() / "hf_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_model_cached(mode: str | None = None, cache_dir: Path | None = None) -> bool:
    """Best-effort check whether the mode's HF model repos already exist on disk."""
    m = normalize_mode(mode)
    root = Path(cache_dir) if cache_dir else hf_cache_dir()
    for repo in TTS_MODES[m]["repos"]:
        repo_dir = root / ("models--" + repo.replace("/", "--"))
        snapshots = repo_dir / "snapshots"
        if not snapshots.is_dir() or not any(snapshots.iterdir()):
            return False
    return True


@contextmanager
def _hf_cache_context(cache_dir: Path):
    """Temporarily route ``huggingface_hub`` downloads to ``cache_dir``.

    Also guards against ``sys.stdout`` / ``sys.stderr`` being ``None`` (as in
    windowed PyInstaller builds) and disables tqdm progress bars when no real
    TTY is attached so ``vieneu`` / ``huggingface_hub`` never raise
    ``AttributeError: 'NoneType' object has no attribute 'write'``.
    """
    cache_str = str(cache_dir.resolve())
    prev_env = os.environ.get("HF_HUB_CACHE")
    prev_pb = os.environ.get("HF_HUB_DISABLE_PROGRESS_BARS")
    os.environ["HF_HUB_CACHE"] = cache_str
    had_null_stdio = sys.stdout is None or sys.stderr is None
    prev_stdout = sys.stdout
    prev_stderr = sys.stderr
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()
    is_tty = False
    try:
        is_tty = bool(getattr(prev_stderr, "isatty", lambda: False)())
    except Exception:
        is_tty = False
    if had_null_stdio or not is_tty:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    hf_constants = None
    prev_const = None
    try:
        import huggingface_hub.constants as _hfc

        hf_constants = _hfc
        prev_const = getattr(_hfc, "HF_HUB_CACHE", None)
        _hfc.HF_HUB_CACHE = cache_str
    except Exception:
        pass
    try:
        yield cache_str
    finally:
        if prev_stdout is None:
            sys.stdout = None
        if prev_stderr is None:
            sys.stderr = None
        if prev_env is None:
            os.environ.pop("HF_HUB_CACHE", None)
        else:
            os.environ["HF_HUB_CACHE"] = prev_env
        if prev_pb is None:
            os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)
        else:
            os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = prev_pb
        if hf_constants is not None and prev_const is not None:
            hf_constants.HF_HUB_CACHE = prev_const


def _looks_like_network_error(exc: BaseException) -> bool:
    chain = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        chain.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    for err in chain:
        name = type(err).__name__.lower()
        msg = str(err).lower()
        if any(tok in name for tok in ("connection", "timeout", "localentrynotfound", "offline", "http")):
            return True
        if any(tok in msg for tok in (
            "connection", "timed out", "getaddrinfo", "name or service not known",
            "failed to resolve", "max retries exceeded", "offline", "huggingface",
        )):
            return True
    return False


def _default_threads() -> int:
    try:
        from dubtool.app import cpu as cpu_mod

        return max(1, int(cpu_mod.thread_budget()))
    except Exception:
        return max(1, min((os.cpu_count() or 4) // 2, 8))


class VieNeuTTSProvider(TTSProvider):
    """Offline Vietnamese TTS using VieNeu-TTS v3 ONNX (CPU).

    The underlying ONNX engine is lazily initialized on the first ``synthesize``
    call and reused across all segments of the run. Call ``close()`` when the
    stage finishes to release ONNX Runtime memory (~480 MB – 2.2 GB).
    """

    name = "vieneu"
    supports_concurrent = False

    def __init__(
        self,
        mode: str = DEFAULT_TTS_MODE,
        *,
        threads: int | None = None,
        steps: int | None = None,
        cache_dir: Path | None = None,
        engine_factory=None,
    ):
        self.mode = normalize_mode(mode)
        self._spec = TTS_MODES[self.mode]
        self.sample_rate: int = int(self._spec["sample_rate"])
        self.precision: str = str(self._spec["precision"])
        self.threads: int = int(threads) if threads and int(threads) > 0 else _default_threads()
        self.steps: int = int(steps) if steps and int(steps) > 0 else int(self._spec.get("steps", 16))
        self.cache_dir: Path | None = Path(cache_dir) if cache_dir else None
        self._engine_factory = engine_factory
        self._engine: Any = None
        self._lock = threading.Lock()
        self.cancel: Any = None

    def _emit_status(self, key: str, fallback: str, **kw: Any) -> None:
        try:
            from dubtool import heartbeat
            from dubtool.app.i18n import tr

            msg = tr(key, **kw)
            if not msg or msg == key:
                msg = fallback.format(**kw)
            heartbeat.emit(msg)
        except Exception:
            pass

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            cdir = self.cache_dir or hf_cache_dir()
            cached = is_model_cached(self.mode, cdir)
            if not cached:
                mb = self._spec["download_mb"]
                log.info(
                    "vieneu: downloading model for mode=%s (~%d MB) into %s (one-time)...",
                    self.mode, mb, cdir,
                )
                self._emit_status(
                    "tts.downloading_model",
                    "Downloading Vietnamese voice model (~{mb} MB, first run only)...",
                    mb=mb,
                )
            else:
                log.info(
                    "vieneu: loading cached model mode=%s (threads=%d) from %s",
                    self.mode, self.threads, cdir,
                )
                self._emit_status(
                    "tts.loading_model",
                    "Loading local Vietnamese voice model...",
                )
            kwargs: dict[str, Any] = {
                "mode": self._spec["vieneu_mode"],
                "threads": self.threads,
            }
            if self.mode == "v3turbo_int8":
                kwargs["backend"] = "onnx"
                kwargs["precision"] = "int8"
            elif self.mode == "v3nano":
                kwargs["steps"] = self.steps
            try:
                with _hf_cache_context(cdir):
                    factory = self._engine_factory
                    if factory is None:
                        from vieneu import Vieneu

                        factory = Vieneu
                    engine = factory(**kwargs)
            except Exception as exc:
                if not cached and _looks_like_network_error(exc):
                    raise TTSEngineInitError(
                        f"Không thể tải model giọng đọc VieNeu ({self.mode}) từ Hugging Face. "
                        f"Cần kết nối Internet cho lần chạy đầu tiên để tải ~{self._spec['download_mb']} MB: {exc}"
                    ) from exc
                raise TTSEngineInitError(
                    f"Không thể khởi tạo engine giọng đọc VieNeu ({self.mode}): {exc}"
                ) from exc

            sr = getattr(engine, "sample_rate", None)
            if isinstance(sr, (int, float)) and sr > 0:
                self.sample_rate = int(sr)
            self._engine = engine
            return self._engine

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        from dubtool.job import StageCancelled

        if self.cancel is not None and self.cancel.is_set():
            raise StageCancelled("tts: cancelled before synthesis")

        cleaned = (text or "").strip()
        if not cleaned:
            raise TTSFailure("refusing to synthesize empty text")

        resolved_voice = resolve_voice(voice, self.mode)
        engine = self._ensure_engine()

        if self.cancel is not None and self.cancel.is_set():
            raise StageCancelled("tts: cancelled after model load")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.parent / f"~vieneu-{uuid.uuid4().hex}.wav"
        try:
            with self._lock:
                wav = engine.infer(
                    cleaned,
                    voice=resolved_voice,
                    max_chars=int(self._spec["max_chars"]),
                    apply_watermark=False,
                )
            arr = np.asarray(wav, dtype=np.float32).reshape(-1)
            if arr.size == 0:
                raise TTSFailure(f"VieNeu returned empty audio for voice={resolved_voice!r}")
            if not np.isfinite(arr).all():
                raise TTSFailure(f"VieNeu returned non-finite audio samples for voice={resolved_voice!r}")
            duration = float(arr.size) / float(self.sample_rate)
            if duration <= 0.05:
                raise TTSFailure(
                    f"VieNeu returned audio too short ({duration:.3f}s) for voice={resolved_voice!r}"
                )
            arr = np.clip(arr, -1.0, 1.0)
            import soundfile as sf

            sf.write(str(tmp_path), arr, self.sample_rate, subtype="PCM_16")
            os.replace(str(tmp_path), str(out_path))
        except (StageCancelled, TTSFailure):
            raise
        except Exception as exc:
            raise TTSFailure(
                f"VieNeu synthesis failed (mode={self.mode}, voice={resolved_voice!r}): {exc}"
            ) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            engine = self._engine
            self._engine = None
        if engine is not None:
            close_fn = getattr(engine, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass
            del engine
            gc.collect()

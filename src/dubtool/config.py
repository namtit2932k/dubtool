"""Configuration loading: config.yaml + .env + CLI overrides.

config.yaml supports scalar top-level keys plus ONE nested level (used for
per-provider `llm:` settings), parsed with a tiny built-in parser instead of
adding a YAML dependency. .env is loaded with python-dotenv (AGENT_UPDATE.md
4.1); real environment variables win over .env values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS: dict = {
    "llm_provider": "gemini",
    "whisper_model": "small",
    "device": "cpu",
    "compute_type": "auto",
    "chars_per_sec": 16.5,
    "max_stretch": 1.15,
    "max_fit_attempts": 3,
    "tts_rate": "+0%",
    "tts_trim_silence": True,
    "tts_silence_threshold_db": -35,
    "tts_silence_margin_s": 0.05,
    "tts_voice": "Trúc Ly",
}

MIX_DEFAULTS: dict = {
    "mode": "static",  # static | duck
    "original_gain_db": -14,
    "dub_gain_db": 2.5,
    "duck": {
        "threshold": 0.03,  # sidechaincompress params, duck mode only
        "ratio": 8,
        "attack_ms": 20,
        "release_ms": 400,
    },
}

SCHEDULE_DEFAULTS: dict = {
    "gap_s": 0.08,      # minimum pause between adjacent Vietnamese clips
    "max_shift_s": 2.0, # cap on how far a clip may be delayed (AGU3 task 3)
}

# Offline Vietnamese TTS (VieNeu-TTS v3 ONNX, CPU).
TTS_DEFAULTS: dict = {
    "provider": "vieneu",
    "mode": "v3turbo_int8",      # v3turbo_int8 (48 kHz) | v3nano (24 kHz, low RAM)
    "steps": 16,                 # Euler steps for v3nano
    "threads": 0,                # 0 = auto from CPU usage setting
    "direct_retries": 1,         # quick retry on transient local error
    "max_chars_per_request": 200,
    "join_silence_s": 0.08,
    "concurrency": 1,
}

# AGENT_UPDATE_8 task 3.3: LLM punctuation restoration (off by default to avoid
# burning sequential pre-translation API calls; local sentence resegmentation still runs).
PUNCTUATE_DEFAULTS: dict = {
    "enabled": "off",  # auto | on | off
    "window_segments": 40,
    "max_missing_ratio": 0.30,
    "max_unpunct_run": 25,
}

# AGENT_UPDATE_8 task 4: filler/vocative tagging.
FILLERS_DEFAULTS: dict = {
    "ask": True,       # GUI shows the suggestions dialog when set; CLI never asks
    "pause_s": 0.25,
}

# AGENT_UPDATE_10.md task 5: job-folder behavior.
JOBS_DEFAULTS: dict = {
    "copy_input": False,   # true restores copying the video into the job dir
    "max_total_mb": 3000,  # housekeeping trims the jobs folder below this
}

# AGENT_UPDATE_10.md task 7.2: disk budget for the project folder.
WORKSPACE_DEFAULTS: dict = {
    "project_max_mb": 1500,
}

# AGENT_UPDATE_8 task 5.2: readable-file uncertainty markers.
READABLE_DEFAULTS: dict = {
    "mark_uncertain": False,
}

from dubtool.llm.prompts import DEFAULT_PRONOUNS

TRANSLATE_DEFAULTS: dict = {
    "unit_mode": "sentence",  # sentence | segment (segment = old behavior)
    "budget_overshoot": 1.15, # enforcement pass triggers above max_chars * this
    "concurrency": 1,         # parallel translation chunk calls (ACU4 A3)
    "pronouns": dict(DEFAULT_PRONOUNS),  # muted per video, kept consistent (AGU4 A2)
    "priority": "balanced",   # AGU8 6.3: balanced | faithful | timing
}

UNITS_DEFAULTS: dict = {
    "max_segments": 4,
    "max_duration_s": 14.0,
    "max_gap_s": 1.0,
    "pause_split_s": 0.5,     # AGU8 4.4: pause closes the unit (unless a comma)
}

ASR_DEFAULTS: dict = {
    "dedupe_min_words": 3,
    "condition_on_previous_text": False,  # used only for new transcriptions
    "word_timestamps": True,     # AGU8 4.2: word times -> words.json
    "sentence_resegment": True,  # AGU8 4.2: cut segments at sentence-final marks
    "initial_prompt": "",        # optional faster-whisper initial_prompt (AGU8 4.1)
}

SUBTITLES_DEFAULTS: dict = {
    "burn": True,
    "font": "Arial",  # Windows system font with Vietnamese coverage
    "size_ratio": 24,   # FontSize ~= video_height / size_ratio (capped on portrait)
    "margin_ratio": 18, # MarginV ~= video_height / margin_ratio
    "bg_box": True,     # translucent dark backing box for readability over source hardsubs
    "crf": 21,
    "preset": "medium",
}

LLM_PROVIDER_DEFAULTS: dict = {
    "gemini": {"model": "gemini-3.5-flash-lite", "chunk_size": 25},
    "ollama": {
        "host": "http://localhost:11434",
        "model": "gemma3:12b",
        "chunk_size": 20,
        "timeout_sec": 300,
    },
    "anthropic": {"model": "claude-sonnet-5-5", "chunk_size": 40},
}

LLM_DEFAULTS: dict = {
    # AGU8 task 2: retry redesign knobs.
    "server_error_backoff_s": [10, 30, 90],
    "split_on_timeout": True,
    "split_min_units": 2,     # AGU11 2.2: default halves floor (was 4)
    "split_min_chars": 300,   # AGU11 2.2: halves also stay above this volume
    # AGU11 2.1: right-sized requests (chars first, then unit count).
    "chunk_max_chars": 1200,
    "chunk_max_units": 8,
    "overload_after": 2,
    "overload_policy": "ask",   # ask | fallback_fast | wait (CLI uses fallback_fast)
    "stage_budget_s": 600,      # AGU11 2.3: waiting budget per stage
    "max_requests_per_run": 120,
    "request_timeout_s": 180,   # Accurate-mode per-request cap
    "request_timeout_s_fast": 90,
}


def _deep_copy(d: dict) -> dict:
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in d.items()}


def _nested_defaults() -> dict:
    """Fresh copies of every nested defaults group (never shared between
    Config instances)."""
    return {
        "llm": _deep_copy(LLM_DEFAULTS) | {pid: dict(vals) for pid, vals in LLM_PROVIDER_DEFAULTS.items()},
        "mix": _deep_copy(MIX_DEFAULTS),
        "subtitles": _deep_copy(SUBTITLES_DEFAULTS),
        "schedule": _deep_copy(SCHEDULE_DEFAULTS),
        "translate": _deep_copy(TRANSLATE_DEFAULTS),
        "units": _deep_copy(UNITS_DEFAULTS),
        "asr": _deep_copy(ASR_DEFAULTS),
        "tts": _deep_copy(TTS_DEFAULTS),
        "punctuate": _deep_copy(PUNCTUATE_DEFAULTS),
        "fillers": _deep_copy(FILLERS_DEFAULTS),
        "readable": _deep_copy(READABLE_DEFAULTS),
        "jobs": _deep_copy(JOBS_DEFAULTS),
        "workspace": _deep_copy(WORKSPACE_DEFAULTS),
    }


_NESTED_KEYS = tuple(_nested_defaults())


def _parse_scalar(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_indent(line: str) -> tuple[int, str]:
    stripped = line.lstrip(" ")
    return len(line) - len(stripped), stripped


def parse_simple_yaml(path: Path) -> dict:
    """Parse scalar keys, nested scalar maps (any depth, e.g. mix.duck.*)
    and simple list blocks (`key:` followed by `- item` lines, used by
    packaging/modes.yaml `models:`)."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    # When a key creates an empty sub-dict, remember it so a following
    # `- item` line at the same indent can turn it into a list.
    list_candidate: tuple[int, dict, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        indent, _ = _parse_indent(raw)
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("- "):
            item = _parse_scalar(line[2:].strip())
            if list_candidate is not None and indent >= list_candidate[0]:
                _indent, parent, lkey = list_candidate
                existing = parent.get(lkey)
                if isinstance(existing, list):
                    existing.append(item)
                elif isinstance(existing, dict) and not existing:
                    # Turn the empty placeholder child created for `key:`
                    # into a list (AGENT_UPDATE_5.md task 1).
                    parent[lkey] = [item]
                    if stack and stack[-1][1] is existing:
                        stack.pop()
                    list_candidate = (indent, parent, lkey)
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        while indent <= stack[-1][0]:
            stack.pop()
            list_candidate = None
        parent = stack[-1][1]
        if value == "":
            child: dict = {}
            parent[key] = child
            list_candidate = (indent, parent, key)
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
            list_candidate = None
    return root


def load_env(root: Path) -> None:
    """Load .env with python-dotenv (real env vars keep precedence), then
    defensively strip whitespace/quotes from known key variables (every
    provider: Gemini, OpenAI — incl. the user's GPT_API_KEY name, Anthropic)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")  # dotenv defaults: real environment wins
    except ImportError:  # pragma: no cover
        pass
    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY", "GPT_API_KEY",
        "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
    ):
        value = os.environ.get(name, None)
        if value is not None:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1].strip()
            os.environ[name] = value


# Backwards-compatible alias (old name was load_dotenv).
load_dotenv = load_env


@dataclass
class Config:
    llm_provider: str = DEFAULTS["llm_provider"]
    whisper_model: str = DEFAULTS["whisper_model"]
    device: str = DEFAULTS["device"]
    compute_type: str = DEFAULTS["compute_type"]
    chars_per_sec: float = DEFAULTS["chars_per_sec"]
    max_stretch: float = DEFAULTS["max_stretch"]
    max_fit_attempts: int = DEFAULTS["max_fit_attempts"]
    tts_voice: str = DEFAULTS["tts_voice"]
    tts_rate: str = DEFAULTS["tts_rate"]
    tts_trim_silence: bool = DEFAULTS["tts_trim_silence"]
    tts_silence_threshold_db: int = DEFAULTS["tts_silence_threshold_db"]
    tts_silence_margin_s: float = DEFAULTS["tts_silence_margin_s"]
    llm: dict = field(default_factory=dict)
    glossary: dict = field(default_factory=dict)
    mix: dict = field(default_factory=lambda: _deep_copy(MIX_DEFAULTS))
    subtitles: dict = field(default_factory=lambda: _deep_copy(SUBTITLES_DEFAULTS))
    schedule: dict = field(default_factory=lambda: _deep_copy(SCHEDULE_DEFAULTS))
    translate: dict = field(default_factory=lambda: _deep_copy(TRANSLATE_DEFAULTS))
    units: dict = field(default_factory=lambda: _deep_copy(UNITS_DEFAULTS))
    asr: dict = field(default_factory=lambda: _deep_copy(ASR_DEFAULTS))
    tts: dict = field(default_factory=lambda: _deep_copy(TTS_DEFAULTS))
    punctuate: dict = field(default_factory=lambda: _deep_copy(PUNCTUATE_DEFAULTS))
    fillers: dict = field(default_factory=lambda: _deep_copy(FILLERS_DEFAULTS))
    readable: dict = field(default_factory=lambda: _deep_copy(READABLE_DEFAULTS))
    jobs: dict = field(default_factory=lambda: _deep_copy(JOBS_DEFAULTS))
    workspace: dict = field(default_factory=lambda: _deep_copy(WORKSPACE_DEFAULTS))

    def provider_config(self, provider_id: str | None = None) -> dict:
        pid = (provider_id or self.llm_provider).lower()
        cfg = dict(LLM_PROVIDER_DEFAULTS.get(pid, {}))
        cfg.update((getattr(self, "llm", {}) or {}).get(pid, {}) or {})
        return cfg

    def chunk_size(self, provider_id: str | None = None) -> int:
        return int(self.provider_config(provider_id).get("chunk_size", 25))


def load(root: Path, overrides: dict | None = None) -> Config:
    data = dict(DEFAULTS)
    data.update(_nested_defaults())

    yaml_file = root / "config.yaml"
    if yaml_file.exists():
        parsed = parse_simple_yaml(yaml_file)
        for key, value in parsed.items():
            if key in _NESTED_KEYS and isinstance(value, dict):
                for sub_key, vals in value.items():
                    if isinstance(vals, dict):
                        data[key].setdefault(sub_key, {})
                        data[key][sub_key].update(vals)
                    else:
                        data[key][sub_key] = vals
            else:
                data[key] = value

    glossary: dict = {}
    glossary_file = root / "glossary.json"
    if glossary_file.exists():
        import json as _json

        glossary = _json.loads(glossary_file.read_text(encoding="utf-8"))
        glossary.pop("_comment", None)

    for key in ("whisper_model", "device", "compute_type", "tts_voice", "llm_provider"):
        env_key = f"DUBTOOL_{key.upper()}"
        if os.environ.get(env_key):
            data[key] = os.environ[env_key]

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            if key in _NESTED_KEYS and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if sub_value is not None:
                        if isinstance(sub_value, dict) and isinstance(data[key].get(sub_key), dict):
                            data[key][sub_key].update(sub_value)
                        else:
                            data[key][sub_key] = sub_value
            else:
                data[key] = value

    from dubtool.tts.vieneu import migrate_voice, normalize_mode

    data["tts_voice"] = migrate_voice(data.get("tts_voice"))
    if isinstance(data.get("tts"), dict):
        data["tts"]["mode"] = normalize_mode(data["tts"].get("mode"))

    kwargs = {name: data[name] for name in DEFAULTS if name in data}
    cfg = Config(**kwargs)
    cfg.llm = data["llm"]
    cfg.glossary = glossary
    cfg.mix = data["mix"]
    cfg.subtitles = data["subtitles"]
    cfg.schedule = data["schedule"]
    cfg.translate = data["translate"]
    cfg.units = data["units"]
    cfg.asr = data["asr"]
    cfg.tts = data["tts"]
    cfg.punctuate = data["punctuate"]
    cfg.fillers = data["fillers"]
    cfg.readable = data["readable"]
    cfg.jobs = data["jobs"]
    cfg.workspace = data["workspace"]
    return cfg

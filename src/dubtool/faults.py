"""Debug-only fault injection for headless verification (AGENT_UPDATE_8.md 2.5;
AGENT_UPDATE_11 adds the tts 'refuse-ids' and 'hang' kinds).

Faults are declared as `stage:kind=value` and passed to the CLI via
`--fault tts:fail-first=6` (repeatable) or env `DUBTOOL_FAULTS`
(comma-separated). They are IGNORED by the GUI and logged as a warning when
active — they exist so the packaged exe can prove recovery paths without
needing the real service to fail. No faults exist for key values or any
secret material.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("dubtool")

ENV_NAME = "DUBTOOL_FAULTS"
_SPEC_RE = re.compile(r"^\s*(tts|llm)\s*:\s*(fail-first|503-first|504-first|timeout-first|hang)\s*=\s*(\d+)\s*$")
_FLAG_RE = re.compile(r"^\s*(llm)\s*:\s*(429-day)(?:=\d+)?\s*$")
_IDS_RE = re.compile(r"^\s*(tts|llm)\s*:\s*refuse-ids\s*=\s*(\d+(?:\s*,\s*\d+)*)\s*$")


def parse_specs(specs: list[str] | None) -> dict[str, dict[str, int]]:
    """Parse CLI specs; invalid specs raise ValueError (fail fast, never
    silently ignored)."""
    faults: dict[str, dict] = {}
    for spec in specs or []:
        ids = _IDS_RE.match(spec)
        if ids:
            faults.setdefault(ids.group(1), {})["refuse-ids"] = [
                int(v) for v in ids.group(2).replace(" ", "").split(",")
            ]
            continue
        m = _FLAG_RE.match(spec)
        if m:
            faults.setdefault(m.group(1), {})[m.group(2)] = 1
            continue
        m = _SPEC_RE.match(spec)
        if not m:
            raise ValueError(
                f"invalid --fault spec {spec!r}; expected 'tts:fail-first=N', "
                "'tts:hang=N', 'tts:refuse-ids=4,12', 'llm:503-first=N', "
                "'llm:504-first=N' or 'llm:429-day'"
            )
        faults.setdefault(m.group(1), {})[m.group(2)] = int(m.group(3))
    return faults


def parse_env() -> dict[str, dict[str, int]]:
    raw = os.environ.get(ENV_NAME, "")
    if not raw.strip():
        return {}
    try:
        if ";" in raw:
            items = [s.strip() for s in raw.split(";") if s.strip()]
        else:
            items = []
            for token in raw.split(","):
                token = token.strip()
                if ":" in token or not items:
                    items.append(token)
                elif "refuse-ids=" in items[-1]:
                    items[-1] += f",{token}"
                else:
                    items.append(token)
        return parse_specs(items)
    except ValueError:
        log.warning("DUBTOOL_FAULTS value ignored (bad format): %r", raw)
        return {}


def set_faults(specs: list[str] | None) -> dict[str, dict[str, int]]:
    """Install parsed CLI faults into the environment (the stages read them
    through get()). Returns the parsed dict so callers can reject GUI usage."""
    faults = parse_specs(specs)
    parts: list[str] = []
    for stage, kinds in faults.items():
        for kind, value in kinds.items():
            if isinstance(value, list):
                val_str = ",".join(str(v) for v in value)
            else:
                val_str = str(value)
            parts.append(f"{stage}:{kind}={val_str}")
    os.environ[ENV_NAME] = ";".join(parts)
    return faults


def get(stage: str) -> dict:
    """Faults currently declared for a stage ({} when none)."""
    return parse_env().get(stage, {})


def announce() -> None:
    faults = parse_env()
    if faults:
        log.warning("fault injection active (debug-only): %s", faults)

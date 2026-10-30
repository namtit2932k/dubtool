"""Debug-only fault injection for headless verification (AGENT_UPDATE_8.md 2.5).

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
_SPEC_RE = re.compile(r"^\s*(tts|llm)\s*:\s*(fail-first|503-first|timeout-first)\s*=\s*(\d+)\s*$")


def parse_specs(specs: list[str] | None) -> dict[str, dict[str, int]]:
    """Parse CLI specs; invalid specs raise ValueError (fail fast, never
    silently ignored)."""
    faults: dict[str, dict[str, int]] = {}
    for spec in specs or []:
        m = _SPEC_RE.match(spec)
        if not m:
            raise ValueError(
                f"invalid --fault spec {spec!r}; expected 'tts:fail-first=N' "
                "or 'llm:503-first=N'"
            )
        faults.setdefault(m.group(1), {})[m.group(2)] = int(m.group(3))
    return faults


def parse_env() -> dict[str, dict[str, int]]:
    raw = os.environ.get(ENV_NAME, "")
    if not raw.strip():
        return {}
    try:
        return parse_specs(raw.split(","))
    except ValueError:
        log.warning("DUBTOOL_FAULTS value ignored (bad format): %r", raw)
        return {}


def set_faults(specs: list[str] | None) -> dict[str, dict[str, int]]:
    """Install parsed CLI faults into the environment (the stages read them
    through get()). Returns the parsed dict so callers can reject GUI usage."""
    faults = parse_specs(specs)
    os.environ[ENV_NAME] = ",".join(
        f"{stage}:{kind}={value}"
        for stage, kinds in faults.items()
        for kind, value in kinds.items()
    )
    return faults


def get(stage: str) -> dict[str, int]:
    """Faults currently declared for a stage ({} when none)."""
    return parse_env().get(stage, {})


def announce() -> None:
    faults = parse_env()
    if faults:
        log.warning("fault injection active (debug-only): %s", faults)

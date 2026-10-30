"""AGENT_UPDATE_8 task 4: filler/vocative detection and the prompt policy.

- `load_settings()` ships `packaging/fillers.yaml` merged with the user's
  override (same file name in the app data folder).
- `tag_fillers(text, words)` is a pure detector: a listed word becomes a
  filler/vocative span only when its position plus context (commas, pauses)
  fits, and it is not inside a protected phrase.
- `suggest_candidates` finds per-video speaker tics (tokens that begin
  sentences/clauses followed by a comma or pause) and `render_policy` builds
  the per-unit instruction block for the translation prompt.
- The user's per-video choices (the dialog) are stored per video in the app
  data folder (hash of path and size, like briefs.json).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("dubtool")

FILLERS_FILE = "fillers.yaml"
CHOICES_FILE = "fillers_choices.json"
SUGGESTIONS_FILE = "suggested_fillers.json"

STOP_WORDS = {
    "i", "the", "and", "but", "so", "it", "we", "you", "they", "that",
    "this", "what", "he", "she", "is", "are", "was", "if", "when",
    "then", "there", "here", "now", "yeah",
}

_DETERMINERS = {
    "a", "an", "the", "my", "your", "her", "his", "our", "their", "its",
    "trans", "good", "little", "some", "other", "two", "three", "young",
}

_AMBIGUOUS = {"like", "well", "know", "mean", "right", "literally", "actually"}

_TOKEN = re.compile(r"[\w'’]+", re.UNICODE)


def _parse_simple_yaml(path: Path) -> dict:
    from yaml import safe_load  # pyyaml ships with the app

    return safe_load(path.read_text(encoding="utf-8")) or {}


def shipped_path() -> Path:
    from dubtool.app import paths

    return paths.bundle_root() / "packaging" / FILLERS_FILE


def user_path() -> Path:
    from dubtool.app import paths

    return paths.settings_dir() / FILLERS_FILE


def load_settings() -> dict:
    """Shipped defaults merged with the user's override file."""
    data: dict = {}
    p = shipped_path()
    if p.exists():
        try:
            data = _parse_simple_yaml(p) or {}
        except Exception as exc:
            log.warning("fillers: shipped %s unreadable (%s)", p, exc)
    up = user_path()
    if up.exists():
        try:
            override = _parse_simple_yaml(up) or {}
            for key in ("vocative", "discourse", "idioms"):
                if isinstance(override.get(key), dict):
                    merged = dict(data.get(key) or {})
                    merged.update(override[key])
                    data[key] = merged
            for key in ("protected",):
                if isinstance(override.get(key), list):
                    data[key] = list(override[key])
        except Exception as exc:
            log.warning("fillers: user %s unreadable (%s)", up, exc)
    data.setdefault("vocative", {})
    data.setdefault("discourse", {})
    data.setdefault("protected", [])
    data.setdefault("idioms", {})
    return data


def _norm(token: str) -> str:
    return token.lower().strip("'\u2019")


# ----------------------------------------------------------------------------
# Per-video choices (the suggestions dialog result), AGU8 5.3.
# hodnota: {token: "drop"|"keep"|"translate_as: <vi text>"}
# ----------------------------------------------------------------------------

def per_video_key(video: Path) -> str:
    """Stable key: path + size (the same file re-detected gives the same key)."""
    import hashlib

    p = Path(video)
    try:
        size = p.stat().st_size
        resolved = str(p.resolve())
    except OSError:
        size = 0
        resolved = str(p)
    digest = hashlib.sha256()
    digest.update(resolved.encode("utf-8", "surrogatepass"))
    digest.update(b"|")
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()[:16]


def _choices_path() -> Path:
    from dubtool.app.paths import settings_dir

    return settings_dir() / CHOICES_FILE


def load_video_choices(video: Path) -> dict:
    return (_read_map(per_video_key(video)))


def save_video_choices(video: Path, choices: dict) -> bool:
    return _write_map(per_video_key(video), choices)


def _read_map(key: str) -> dict:
    try:
        data = json.loads(_choices_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entry = data.get(key) if isinstance(data, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def _write_map(key: str, value: dict) -> bool:
    try:
        path = _choices_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if value:
            data[key] = dict(value)
        else:
            data.pop(key, None)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False


def write_suggestions(job_dir: Path, suggestions: list[dict]) -> Path:
    """Save `suggested_fillers.json` in the job directory."""
    path = Path(job_dir) / SUGGESTIONS_FILE
    try:
        path.write_text(
            json.dumps(suggestions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return path


def read_suggestions(job_dir: Path) -> list[dict]:
    try:
        data = json.loads((Path(job_dir) / SUGGESTIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


# ----------------------------------------------------------------------------
# Detector
# ----------------------------------------------------------------------------

def _word_mids(words_with_times) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    for w in words_with_times or []:
        if isinstance(w, dict):
            tok = str(w.get("w") or w.get("word") or "").strip()
        else:
            tok = str(w or "").strip()
        if tok:
            out.append((_norm(tok).rstrip("."), float(w.get("start", 0.0)), float(w.get("end", 0.0))))
    return out


def _pause_before(mids, index: int) -> float:
    if not mids or index <= 0 or index >= len(mids):
        return 0.0
    prev, cur = mids[index - 1], mids[index]
    return max(0.0, cur[1] - prev[2])


def _pause_after(mids, index: int) -> float:
    if not mids or index < 0 or index >= len(mids) - 1:
        return 0.0
    cur, nxt = mids[index], mids[index + 1]
    return max(0.0, nxt[1] - cur[2])


def _is_protected(norm_tokens: list[str], index: int, protected: list[str]) -> bool:
    """True when the token sits inside one of the protected phrases."""
    win = len(norm_tokens)
    for phrase in protected:
        pat = [_norm(t) for t in re.findall(r"[\w'\u2019]+", phrase.lower())]
        if not pat:
            continue
        n = len(pat)
        if win < n:
            continue
        for start in range(max(0, index - n + 1), min(index + 1, win - n + 1)):
            window = norm_tokens[start : start + n]
            if window == pat:
                return True
    return False


def _classify(
    kind: str,
    action: str,
    *, starting: bool, set_off: bool, depends: bool, is_end: bool,
) -> bool:
    """Context rules shared by the phrase and single-word paths."""
    if kind == "vocative":
        ok = (starting or set_off) and (set_off or is_end) and not depends
        return ok
    # discourse
    if is_end or starting or (set_off and starting):
        return True
    return bool(set_off)


def tag_fillers(
    sentence_text: str,
    words_with_times: list[dict] | None = None,
    settings: dict | None = None,
    pause_s: float = 0.25,
) -> list[dict]:
    """Pure detector (AGU8 5.2): candidate spans only when position + context
    fit. Returns [{"start", "end", "kind", "text", "action"}]; kind is
    'vocative' | 'discourse', action 'drop' | 'keep' | 'translate_as ...'.
    Start/end are token indexes into the text.
    """
    settings = settings or load_settings()
    protected = [p.lower() for p in (settings.get("protected") or [])]
    tables: dict[str, dict[str, str]] = {"vocative": {}, "discourse": {}}
    for kind in ("vocative", "discourse"):
        for key, value in (settings.get(kind) or {}).items():
            tables[kind][key.lower().strip()] = str(value or "drop")

    text = sentence_text or ""
    found = list(_TOKEN.finditer(text))
    if not found:
        return []
    tokens = [_norm(m.group(0)) for m in found]
    raw_tokens = [m.group(0) for m in found]
    # Real punctuation positions matter: "Girl, I support..." has a comma
    # right after the token span.
    def comma_after(end: int) -> bool:
        j = end
        while j < len(text) and text[j] in " \t":
            j += 1
        return j < len(text) and text[j] == ","

    # Multi-word phrase keys are matched first ("you know", "okay so").
    phrases: list[tuple[tuple[str, ...], str, str]] = []
    for kind, table in tables.items():
        for key, action in table.items():
            parts = tuple(_norm(t) for t in key.split())
            if len(parts) > 1:
                phrases.append((parts, kind, action))
    phrases.sort(key=lambda item: -len(item[0]))

    mids = _word_mids(words_with_times)
    # For a pause check we need the token's position inside mids: match by
    # normalized word sequence where possible.
    def token_pause(i: int) -> float:
        word = tokens[i]
        # find the matching mids index by walking the sequence
        pos = _mid_index.get(i)
        if pos is None:
            return 0.0
        return max(_pause_before(mids, pos), _pause_after(mids, pos))

    _mid_index = {}
    cursor = 0
    for i, tok in enumerate(tokens):
        while cursor < len(mids) and (
            mids[cursor][0] != tok and not mids[cursor][0].startswith(tok)
        ):
            cursor += 1
        if cursor < len(mids) and (
            mids[cursor][0] == tok or mids[cursor][0].startswith(tok)
        ):
            _mid_index[i] = cursor
            cursor += 1

    spans: list[dict] = []
    i = 0
    while i < len(tokens):
        matched = False
        for parts, kind, action in phrases:
            n = len(parts)
            if i + n > len(tokens) or tuple(tokens[i : i + n]) != parts:
                continue
            phrase_text = text[found[i].start() : found[i + n - 1].end()]
            norm_window = tokens[i : i + n]
            if any(_is_protected(norm_window, j, protected) for j in range(n)):
                continue
            prev_tok = tokens[i - 1] if i > 0 else ""
            starting = prev_tok.endswith((",", ";", ":", ".")) or i == 0
            is_end = i + n >= len(tokens)
            set_off = comma_after(found[i + n - 1].end()) or token_pause(i) >= pause_s
            depends = prev_tok in _DETERMINERS or prev_tok.endswith("'s") or prev_tok.endswith("\u2019s")
            if _classify(kind, action, starting=starting, set_off=set_off,
                         depends=depends, is_end=is_end):
                spans.append({
                    "start": i, "end": i + n, "kind": kind,
                    "text": phrase_text, "action": action,
                })
            matched = True
            i += n
            break
        if matched:
            continue
        low = tokens[i]
        entry = None
        kind = None
        for candidate in ("vocative", "discourse"):
            if low in tables[candidate] and len(low) > 0:
                kind = candidate
                entry = tables[candidate][low]
                break
        if kind is None:
            i += 1
            continue
        prev_raw = raw_tokens[i - 1] if i > 0 else ""
        prev_tok = tokens[i - 1] if i > 0 else ""
        starting = prev_raw.endswith((",", ";", ":", ".")) or i == 0
        is_end = i + 1 >= len(tokens)
        set_off = comma_after(found[i].end()) or token_pause(i) >= pause_s
        depends = (
            prev_tok in _DETERMINERS or prev_tok.endswith("'s")
            or prev_tok.endswith("\u2019s")
        )
        if _is_protected(tokens, i, protected):
            i += 1
            continue
        if _classify(kind, entry, starting=starting, set_off=set_off,
                     depends=depends, is_end=is_end):
            spans.append({
                "start": i, "end": i + 1, "kind": kind,
                "text": raw_tokens[i], "action": entry,
            })
        i += 1
    return spans


def suggest_candidates(
    segments: list[dict], min_ratio: float = 0.03, top: int = 8
) -> list[dict]:
    """AGU8 5.3: per-video suggestions for sentence-initial tics.

    Counts tokens that begin sentences/clauses followed by a comma or a
    pause, excluding the stop list. Suggests tokens with
    count >= max(5, 3% of sentences)."""
    counts: dict[str, dict] = {}
    sentences = 0
    for seg in segments:
        text = seg.get("text") or ""
        found = list(_TOKEN.finditer(text))
        if not found:
            continue
        sentences += 1
        mids = _word_mids(seg.get("words") or [])
        _mid_index = {}
        cursor = 0
        toks = [_norm(m.group(0)) for m in found]
        for i, tok in enumerate(toks):
            while cursor < len(mids) and mids[cursor][0] != tok:
                cursor += 1
            if cursor < len(mids) and mids[cursor][0] == tok:
                _mid_index[i] = cursor
                cursor += 1
        for i, tok in enumerate(toks):
            if tok in STOP_WORDS or tok in _DETERMINERS or not tok:
                continue
            prev = toks[i - 1] if i > 0 else ""
            at_start = prev in ("",) or prev.endswith((";", ".",))
            if not at_start and i not in _mid_index:
                continue
            set_off = (
                _comma_after_span(text, found[i].end())
                or (max(_pause_before(mids, _mid_index[i]), _pause_after(mids, _mid_index[i])) >= 0.25
                    if i in _mid_index else False)
            )
            if not set_off:
                continue
            entry = counts.setdefault(tok, {"count": 0, "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append({
                    "seg_id": seg.get("id"),
                    "time": seg.get("start"),
                    "text": text[:90],
                })
    threshold = max(5, sentences * min_ratio)
    out = [
        {"token": tok, "count": e["count"], "examples": e["examples"]}
        for tok, e in sorted(counts.items(), key=lambda kv: -kv[1]["count"])
        if e["count"] >= threshold
    ]
    return out[:top]


def _comma_after_span(text: str, end: int) -> bool:
    j = end
    while j < len(text) and text[j] in " \t":
        j += 1
    return j < len(text) and text[j] == ","


def resolve_actions(
    suggestions: list[dict],
    settings: dict | None = None,
    user_choices: dict | None = None,
) -> dict[str, str]:
    """Actions per token: user choices win, then shipped defaults."""
    settings = settings or load_settings()
    defaults = {k.lower(): str(v) for k, v in
                ((settings.get("vocative") or {}).items())}
    defaults.update({k.lower(): str(v) for k, v in
                     ((settings.get("discourse") or {}).items())})
    choices = {k.lower(): str(v) for k, v in (user_choices or {}).items()}
    out: dict[str, str] = {}
    for sug in suggestions:
        tok = str(sug.get("token", "")).lower()
        if tok in choices:
            out[tok] = choices[tok]
        else:
            out[tok] = defaults.get(tok, "drop")
    return out


def render_policy(
    spans: list[dict] | None = None,
    actions: dict[str, str] | None = None,
    settings: dict | None = None,
) -> str:
    """Plain-instruction policy block (AGU8 5.4): the rules plus, per unit,
    the detected spans."""
    settings = settings or load_settings()
    protected = list(settings.get("protected") or [])
    idioms = dict(settings.get("idioms") or {})
    lines: list[str] = ["Filler word policy:"]
    if actions:
        for tok, action in actions.items():
            if action == "drop":
                lines.append(
                    f"- The word {tok!r} used alone at the start or end of a clause is a vocal filler: do not translate it."
                )
            elif action == "keep":
                lines.append(
                    f"- The word {tok!r} used alone like this: translate it by meaning."
                )
            elif action.startswith("translate_as"):
                target = action.split(":", 1)[-1].strip()
                lines.append(
                    f"- The word {tok!r} used alone like this: write exactly {target!r} there."
                )
    lines.append(
        "- Omit discourse fillers like 'um', 'uh', 'you know' when they stand alone."
    )
    if protected:
        lines.append(
            "- If the word is used as a real noun or inside an idiom (for example "
            + ", ".join(repr(p) for p in protected[:4])
            + "), translate it normally, never as a filler."
        )
    if idioms:
        pairs = "; ".join(f"{k!r} = {v!r}" for k, v in list(idioms.items())[:4])
        lines.append(f"- Translate idioms by meaning, never word by word: {pairs}.")
    lines.append(
        "- When shortening: remove fillers first, then repeated words, then "
        "modifiers; never remove the subject, a negation, or the key noun phrase."
    )
    if spans:
        brs = ", ".join(
            f"{s['text']!r} ({s['kind']}, {s['action']})" for s in spans
        )
        lines.insert(1, f"- Detected in this unit: {brs}.")
    return "\n".join(lines)

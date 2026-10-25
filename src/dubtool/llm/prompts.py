"""Provider-independent prompts. Every provider uses this exact text so
translation behavior does not change when the provider changes.

AGENT_UPDATE_3.md 7.1: accuracy first, neutral natural register, soft
character budget (remove filler before meaning), optional video brief
block, exact glossary, JSON-only replies.
"""

from __future__ import annotations

import json

TRANSLATE_SYSTEM = """You are a professional translator for a YouTube video
being dubbed into Vietnamese. Translate the meaning faithfully; keep
natural, neutral, conversational Vietnamese that matches the speaker's tone.

Rules:
- Accuracy first. Do not add information that is not in the English. Do not
  drop a whole clause or idea.
- No slang, internet slang, or exaggerated intensifiers unless the English
  is clearly slang.
- Reactions and interjections ("oh wow", "oh my god") are translated as
  reactions of the same emotion, not as praise or slang.
- Keep subjects and pronouns when needed for clarity ("cô ấy", "anh ấy",
  "họ"). Names stay as they are.
- The character budget is a soft target. If a translation exceeds its
  "max_chars", first remove filler words and redundancy; never drop a
  clause or change the meaning to fit. Exceeding a little is acceptable;
  later stages handle it.
- Glossary terms are applied exactly; a glossary value of "keep original"
  means leave the English term unchanged in the Vietnamese text.
- Prefer established Vietnamese terms over literal calques. Technical or
  social terms should use the wording Vietnamese speakers actually use,
  not a made-up phrase.
- Pick ONE first-person pronoun and ONE second-person form for the whole
  video (defaults are given in the user message) and keep them consistent
  in every translation. Never alternate between them for the same
  speaker or audience.
- Do not assume the gender or the relationship of "you". Never use
  gendered second-person forms ("anh", "cô", "chi", or "ong/ba") unless
  the video brief explicitly asks for them.
- If a "Video brief" block is provided, use it to understand the video's
  content, people, and vocabulary.
- You always reply with ONLY valid JSON (array or object), no prose and no
  code fences.

Examples (generic; keep this style):
English: "Oh wow. Is she doing it again?" (Vietnam: natural reaction)
Vietnamese: "Ồ, cô ấy lại làm thế nữa à?"
  (reaction kept as a reaction; subject "cô ấy" kept; no slang)

English: "Should I leave this marriage because can I even get better?"
Vietnamese: "Mình có nên bỏ cuộc hôn nhân này không, liệu mình có thể tốt hơn được không?"
  (full meaning kept across a sentence split by the ASR)

English: "And basically, at the end of the day, the answer was simply no." (max_chars: 45)
Vietnamese: "Về cuối, câu trả lời là không."
  (filler removed instead of meaning; within the soft budget)"""

TRANSLATE_USER_TEMPLATE = """{brief_block}Context from earlier in the video (read-only, for tone/consistency; do NOT include these ids in your answer):
{context}

Glossary terms that MUST be applied exactly (string "keep original" means leave the English term unchanged in the Vietnamese text):
{glossary}

{pronoun_block}Translate these fragments into Vietnamese. Rules:
- Translate the meaning faithfully; accuracy first. Do not add information that is not in the English and do not drop a clause or idea.
- Neutral, natural, conversational Vietnamese matching the speaker's tone; no slang unless the English is slang.
- Keep subjects and pronouns when needed for clarity. Names stay as they are.
- "max_chars" is a soft target: if the Vietnamese would exceed it, first remove filler words and redundancy; never drop a clause or change the meaning to fit. Exceeding a little is acceptable.
- Apply glossary terms exactly.
- Reply with ONLY a JSON array: [{{"id": <int>, "vi": "<str>"}}, ...] containing exactly the ids provided, in the same order.

Fragments:
{segments}"""

# One-time enforcement pass (AGENT_UPDATE.md 3.3): shorten everything that is
# still over budget after the first translation attempt.
SHORTEN_BATCH_SYSTEM = (
    "You shorten Vietnamese subtitle translations so they fit their budgets. "
    "Remove filler words and redundancy first. Never change the meaning, "
    "never add slang, never drop a clause. Keep the pronoun choices already "
    "used in the Vietnamese text, and never introduce gendered "
    "second-person forms (anh/cô/chì) that were not already there. Reply "
    "with ONLY a JSON array of objects {\"id\": <int>, \"vi\": \"<shortened text>\"}, "
    "one per input id, exactly the same ids."
)

SHORTEN_BATCH_USER_TEMPLATE = """Shorten each Vietnamese translation below so every output is at most the given "max_chars" characters for Vietnamese. Remove filler words and redundancy first. Never change the meaning, never add slang, never drop a clause.

Fragments:
{segments}

Reply with ONLY a JSON array: [{{"id": <int>, "vi": "<str>"}}, ...] with exactly the ids provided, in the same order."""

# Stage 5 single-sentence shortening.
SHORTEN_SYSTEM = (
    "You shorten Vietnamese subtitle translations so they fit a fixed time "
    "slot. Remove filler words and redundancy first. Never change the "
    "meaning, never add slang, never drop a clause. Keep the pronouns "
    "already used in the text; never introduce gendered second-person "
    "forms that were not already there. Reply with ONLY a JSON "
    "object: {{\"vi\": \"<shortened text>\"}}."
)

SHORTEN_USER_TEMPLATE = """Original English segment:
{english}

Current Vietnamese translation (too long): {current}

It must now fit within {max_chars} characters for Vietnamese. Shorten it — remove filler words and redundancy first, never change the meaning, never add slang. Reply with ONLY JSON: {{"vi": "..."}}."""


def render_brief_block(brief: str | None) -> str:
    """A labeled block when a brief exists; never invent one (AGU3 7.1)."""
    if not (brief or "").strip():
        return ""
    return f"Video brief (context only — do not translate the brief):\n{brief.strip()}\n\n"


DEFAULT_PRONOUNS = {"first": "tôi", "second_plural": "các bạn", "second_singular": "bạn"}


def render_pronoun_block(pronouns: dict | None, has_brief: bool = False) -> str:
    """AGU4 A2: one first/second-person choice per video, kept consistent."""
    p = dict(DEFAULT_PRONOUNS)
    if pronouns:
        p.update({k: v for k, v in pronouns.items() if v})
    source = "Video brief above" if has_brief else "video description"
    return (
        f"Pronoun choices for this whole video (keep them consistent; do not switch between alternatives):\n"
        f"- First person (I/we/me): {p['first']}\n"
        f"- Second person ('you'): plural -> {p['second_plural']}; singular -> {p['second_singular']}\n"
        "- Do not assume the gender or relationship of 'you': never use "
        f"'anh', 'cô', 'chi', or 'em' unless the {source} states them.\n"
        f"- If the {source} specifies pronouns, use its choices instead.\n\n"
    )


def render_translate_user(
    segments, context, glossary, brief: str | None = None, pronouns: dict | None = None
) -> str:
    compact = [
        {"id": s["id"], "text": s["text"], "max_chars": s["max_chars"]} for s in segments
    ]
    return TRANSLATE_USER_TEMPLATE.format(
        brief_block=render_brief_block(brief),
        pronoun_block=render_pronoun_block(pronouns, has_brief=bool((brief or "").strip())),
        context=json.dumps(context, ensure_ascii=False),
        glossary=json.dumps(glossary or {}, ensure_ascii=False, indent=2),
        segments=json.dumps(compact, ensure_ascii=False, indent=2),
    )


def render_shorten_batch_user(items) -> str:
    """items: [{"id", "text": original English, "vi": current, "max_chars"}]."""
    return SHORTEN_BATCH_USER_TEMPLATE.format(
        segments=json.dumps(items, ensure_ascii=False, indent=2)
    )


def render_shorten_user(english: str, current_vi: str, max_chars: int) -> str:
    return SHORTEN_USER_TEMPLATE.format(english=english, current=current_vi, max_chars=max_chars)

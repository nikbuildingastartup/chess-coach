"""Shared tolerant JSON parsing for structured LLM responses.

Both `app.focus` (cross-game daily focus) and `app.coaching` (per-game
coaching summary) ask fal.ai/Haiku for strict JSON with the same
`headline`/`explanation`/`recommendation` shape and need to parse the
response the same forgiving way (models occasionally wrap the JSON in a
markdown code fence, or add stray whitespace around it). Kept as a small
standalone module rather than living in either call site so neither one
has to import internals from the other.
"""

import json


def parse_structured_llm_response(content: str) -> dict[str, str | None] | None:
    """Tolerantly parse a model's response into
    `{headline, explanation, recommendation}`.

    Strips a leading/trailing markdown code fence (with or without a
    `json` language tag) if present, then extracts the first `{...}`
    substring and attempts `json.loads` on it.

    Returns `None` if no valid JSON object could be extracted at all --
    callers should fall back to using the raw text as `explanation` in
    that case, never raising.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return {
        "headline": parsed.get("headline"),
        "explanation": parsed.get("explanation"),
        "recommendation": parsed.get("recommendation"),
    }

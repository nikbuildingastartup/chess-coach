import logging
from typing import Any

from openai import OpenAI

from app.config import settings
from app.llm_json import parse_structured_llm_response

logger = logging.getLogger(__name__)

FAL_BASE_URL = "https://fal.run/openrouter/router/openai/v1"
FAL_MODEL = "anthropic/claude-haiku-4.5"

SYSTEM_PROMPT = (
    "You are an encouraging, direct chess coach. Given one game's move-by-move "
    "analysis, respond with STRICT JSON only -- no markdown, no prose outside "
    "the JSON -- with exactly these string fields: \"headline\" (a short, "
    "specific name for the most important mistake pattern in THIS game, under "
    "10 words), \"explanation\" (2-4 sentences naming the most important "
    "recurring mistakes in THIS game -- do not claim these are patterns across "
    "multiple games, you are only shown one game here; be specific and "
    "actionable, not generic), and \"recommendation\" (one concrete, "
    "actionable next step the player can practice). The player you are "
    "coaching played White -- only comment on White's moves, never on the "
    "opponent's (Black's) moves. Do not wrap the JSON in a code fence."
)

# The human always plays White in the Play Module (see PlayPanel.tsx); the
# engine always plays Black. Only White's flagged moves are the user's own
# mistakes -- Black's are the engine's own play and must not be fed to the
# coaching prompt as if they were the player's errors.
USER_SIDE = "white"


def _build_user_prompt(pgn: str, analysis: list[dict[str, Any]], result: str) -> str:
    flagged = [
        entry
        for entry in analysis
        if entry.get("classification") in ("blunder", "mistake")
        and entry.get("side") == USER_SIDE
    ]

    if flagged:
        lines = []
        for entry in flagged:
            lines.append(
                f"- Move {entry.get('move_number')}: {entry.get('san')} "
                f"({entry.get('classification')}), eval after move: "
                f"{entry.get('eval_cp')} centipawns, best move was "
                f"{entry.get('best_move')}"
            )
        mistakes_section = "\n".join(lines)
    else:
        mistakes_section = "No blunders or mistakes were flagged in this game."

    return (
        "The player you are coaching played White in this game; the "
        "opponent (Black) was the in-app Stockfish engine. Only coach the "
        "player on their own (White's) moves below.\n\n"
        f"Game result: {result}\n\n"
        f"PGN:\n{pgn}\n\n"
        f"Flagged moves (White's only):\n{mistakes_section}\n\n"
        "Respond with the JSON object now."
    )


def generate_coaching_summary(
    pgn: str, analysis: list[dict[str, Any]], result: str
) -> dict[str, str | None] | None:
    """Generate a structured `{headline, explanation, recommendation}`
    coaching summary for one game.

    Returns None (logged, never raised) if no fal.ai API key is configured
    or if the API call itself fails for any reason -- callers must treat
    this as a purely optional enhancement on top of the always-available
    structured `analysis`, with no non-LLM fallback text (unlike
    `app.focus.generate_daily_focus`, which does have a stats-based
    fallback -- there's no equivalent deterministic summary to build for a
    single game's flagged moves, so "no summary" is the right degradation
    here).

    If the API call succeeds but the response isn't valid JSON, the raw
    response text is used as `explanation` (mirroring
    `generate_daily_focus`'s tolerant-parse fallback) with
    `headline`/`recommendation` left `None`, rather than discarding it.
    """
    if not settings.fal_api_key:
        logger.info("Skipping coaching summary generation: no FAL_KEY configured.")
        return None

    try:
        client = OpenAI(
            base_url=FAL_BASE_URL,
            api_key="not-needed",
            default_headers={"Authorization": f"Key {settings.fal_api_key}"},
            timeout=20.0,
            max_retries=1,
        )
        response = client.chat.completions.create(
            model=FAL_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(pgn, analysis, result)},
            ],
            max_tokens=300,
        )
        content = response.choices[0].message.content or ""
    except Exception:
        logger.exception("Failed to generate coaching summary via fal.ai.")
        return None

    parsed = parse_structured_llm_response(content)
    if parsed is None:
        logger.warning(
            "Coaching summary response was not valid JSON; using raw text as explanation."
        )
        return {"headline": None, "explanation": content.strip(), "recommendation": None}

    return parsed

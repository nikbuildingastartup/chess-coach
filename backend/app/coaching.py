import logging
from typing import Any

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

FAL_BASE_URL = "https://fal.run/openrouter/router/openai/v1"
FAL_MODEL = "anthropic/claude-haiku-4.5"

SYSTEM_PROMPT = (
    "You are an encouraging, direct chess coach. Given one game's move-by-move "
    "analysis, write 2-4 sentences in English that name the most important "
    "recurring mistakes in THIS game. Do not claim these are patterns across "
    "multiple games -- you are only shown one game here. Be specific and "
    "actionable, not generic."
)


def _build_user_prompt(pgn: str, analysis: list[dict[str, Any]], result: str) -> str:
    flagged = [
        entry for entry in analysis if entry.get("classification") in ("blunder", "mistake")
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
        f"Game result: {result}\n\n"
        f"PGN:\n{pgn}\n\n"
        f"Flagged moves:\n{mistakes_section}\n\n"
        "Write the coaching paragraph now."
    )


def generate_coaching_summary(pgn: str, analysis: list[dict[str, Any]], result: str) -> str | None:
    """Generate a short natural-language coaching paragraph for one game.

    Returns None (logged, never raised) if no fal.ai API key is configured
    or if the API call fails for any reason -- callers must treat this as a
    purely optional enhancement on top of the always-available structured
    `analysis`.
    """
    if not settings.fal_api_key:
        logger.info("Skipping coaching summary generation: no FAL_KEY configured.")
        return None

    try:
        client = OpenAI(
            base_url=FAL_BASE_URL,
            api_key="not-needed",
            default_headers={"Authorization": f"Key {settings.fal_api_key}"},
        )
        response = client.chat.completions.create(
            model=FAL_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(pgn, analysis, result)},
            ],
            max_tokens=300,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception:
        logger.exception("Failed to generate coaching summary via fal.ai.")
        return None

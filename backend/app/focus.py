"""Cross-game daily focus generation: turns `weakness_profile`'s
deterministic aggregation into a structured, coached recommendation (via
fal.ai/Haiku, with a deterministic non-LLM fallback), plus extraction of
concrete practice positions from the flagged moves behind it.

Kept separate from `app.coaching` (per-game coaching text) -- the two
generate structurally identical `{headline, explanation, recommendation}`
output but from different inputs (cross-game stats vs. one game's PGN).
The shared tolerant-JSON-parsing logic lives in `app.llm_json`.
"""

import logging
from typing import Any

from openai import OpenAI
from sqlmodel import Session

from app.chess_engine import fen_before_move
from app.coaching import FAL_BASE_URL, FAL_MODEL
from app.config import settings
from app.llm_json import parse_structured_llm_response
from app.llm_usage import record_llm_usage
from app.models import Game

logger = logging.getLogger(__name__)

# Maximum number of practice positions to extract per daily focus -- more
# than a handful would turn "one focused exercise session" into a chore.
PRACTICE_POSITIONS_MAX = 5

SYSTEM_PROMPT = (
    "You are an encouraging, direct chess coach. You are given a "
    "statistical summary of a player's most common mistake pattern across "
    "several recent games (which phase of the game it happens in, how "
    "often, and a few concrete example moves). Respond with STRICT JSON "
    "only -- no markdown, no prose outside the JSON -- with exactly these "
    "string fields: \"headline\" (a short, specific name for the pattern, "
    "under 10 words), \"explanation\" (2-3 sentences on why this pattern "
    "is happening, referencing the phase and frequency), and "
    "\"recommendation\" (one concrete, actionable next step the player "
    "can practice today). Do not wrap the JSON in a code fence."
)


def _build_user_prompt(aggregated_data: dict[str, Any]) -> str:
    top = aggregated_data.get("top_pattern") or {}
    lines = [
        f"Analyzed games in this window: {aggregated_data.get('total_games')}",
        f"Total flagged (blunder/mistake) moves: {aggregated_data.get('total_flagged')}",
        f"Most frequent pattern: {top.get('classification')} in the {top.get('phase')} "
        f"({top.get('count')} occurrences)",
        "Counts by phase:classification pattern:",
    ]
    for pattern, count in (aggregated_data.get("counts_by_pattern") or {}).items():
        lines.append(f"  - {pattern}: {count}")

    lines.append("Example moves for the most frequent pattern (most recent first):")
    for move in (aggregated_data.get("top_pattern_moves") or [])[:5]:
        lines.append(
            f"  - Move {move.get('move_number')}: {move.get('san')} "
            f"({move.get('classification')}), eval after move: "
            f"{move.get('eval_cp')} centipawns, best move was {move.get('best_move')}"
        )

    lines.append("\nRespond with the JSON object now.")
    return "\n".join(lines)


def _fallback_focus(aggregated_data: dict[str, Any]) -> dict[str, str | None]:
    """Deterministic, non-LLM focus text built directly from the aggregated
    stats. Used whenever the LLM call is skipped or fails outright (as
    opposed to succeeding but returning unparseable JSON -- see
    `generate_daily_focus`)."""
    total_games = aggregated_data.get("total_games", 0)
    total_flagged = aggregated_data.get("total_flagged", 0)
    top = aggregated_data.get("top_pattern")

    if not top or total_flagged == 0:
        return {
            "headline": "No major recurring issues found",
            "explanation": (
                f"Across your last {total_games} analyzed games, no recurring "
                "blunders or mistakes stood out often enough to call out a "
                "single pattern."
            ),
            "recommendation": "Keep playing -- check back after a few more games.",
        }

    phase = top["phase"]
    classification = top["classification"]
    count = top["count"]

    return {
        "headline": f"Recurring {classification}s in the {phase}",
        "explanation": (
            f"You most often made a {classification} in the {phase} -- "
            f"{count} of {total_flagged} flagged moves across your last "
            f"{total_games} analyzed games."
        ),
        "recommendation": (
            f"Focus your next practice session on {phase} positions and slow "
            "down before committing to moves in that phase."
        ),
    }


def generate_daily_focus(
    aggregated_data: dict[str, Any], session: Session
) -> dict[str, str | None]:
    """Generate a structured `{headline, explanation, recommendation}` daily
    focus from `aggregate_weakness_data`'s output.

    Never raises. If `FAL_KEY` isn't configured or the API call fails for
    any reason, falls back to a deterministic stats-based text. If the API
    call succeeds but the response isn't valid JSON, the raw response text
    is used as `explanation` (with `headline`/`recommendation` left `None`)
    rather than discarding it -- the model still said something useful, it
    just didn't follow the requested shape.
    """
    if not settings.fal_api_key:
        logger.info("Skipping daily focus generation: no FAL_KEY configured.")
        return _fallback_focus(aggregated_data)

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
                {"role": "user", "content": _build_user_prompt(aggregated_data)},
            ],
            max_tokens=300,
        )
        content = response.choices[0].message.content or ""
        try:
            record_llm_usage(session, "focus", FAL_MODEL, response.usage)
        except Exception:
            # `record_llm_usage` is documented to never raise, but this
            # call site is still guarded independently -- usage recording
            # is a purely optional side effect and must never turn an
            # already-successful API response into a failed one, even if
            # that contract is ever violated (e.g. by a bug, or in tests).
            logger.exception("record_llm_usage raised unexpectedly for call_site=focus.")
    except Exception:
        logger.exception("Failed to generate daily focus via fal.ai.")
        return _fallback_focus(aggregated_data)

    parsed = parse_structured_llm_response(content)
    if parsed is None:
        logger.warning(
            "Daily focus response was not valid JSON; using raw text as explanation."
        )
        return {"headline": None, "explanation": content.strip(), "recommendation": None}

    return parsed


def extract_practice_positions(
    games: list[Game], aggregated_data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Extract up to `PRACTICE_POSITIONS_MAX` practice positions from the
    flagged moves of `aggregated_data`'s most frequent pattern.

    `top_pattern_moves` is already ordered blunder-before-mistake (via the
    pattern-level tie-break in `aggregate_weakness_data`) and newest game
    first. Each returned dict has `fen`, `played_move`, `best_move`,
    `classification`.
    """
    games_by_id = {game.id: game for game in games}
    positions: list[dict[str, Any]] = []

    for move in aggregated_data.get("top_pattern_moves") or []:
        if len(positions) >= PRACTICE_POSITIONS_MAX:
            break

        game = games_by_id.get(move["game_id"])
        if game is None:
            continue
        try:
            fen = fen_before_move(game.pgn, move["move_number"], move["side"])
        except RuntimeError:
            logger.exception(
                "Failed to derive FEN for practice position (game_id=%s, "
                "move_number=%s, side=%s); skipping this position.",
                move["game_id"],
                move["move_number"],
                move["side"],
            )
            continue

        positions.append(
            {
                "fen": fen,
                "played_move": move["san"],
                "best_move": move["best_move"],
                "classification": move["classification"],
            }
        )

    return positions

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
from sqlmodel import Session, select

from app.chess_engine import fen_before_move
from app.coaching import FAL_BASE_URL, FAL_MODEL
from app.config import settings
from app.llm_json import parse_structured_llm_response
from app.llm_usage import record_llm_usage
from app.models import Game, PracticeAttempt

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
    games: list[Game],
    aggregated_data: dict[str, Any],
    session: Session | None = None,
    max_positions: int = PRACTICE_POSITIONS_MAX,
) -> dict[str, Any]:
    """Extract up to `max_positions` practice positions, interleaved
    round-robin across the ranked weakness patterns in
    `aggregated_data["moves_by_pattern"]` (falls back to the single
    `top_pattern`/`top_pattern_moves` pair if that key is absent, so
    older-shaped `aggregated_data` still works).

    If `session` is given, positions with a persisted `PracticeAttempt`
    row marked `solved=True` are excluded, and positions with an *open*
    attempt (`solved=False`, `attempts_count > 0` -- i.e. previously
    answered wrong) are moved to the front of the queue ahead of
    never-attempted positions, so incorrect puzzles resurface in a later
    session. Without a session, no filtering or reordering happens.

    Returns `{"positions": list[dict], "skipped_count": int}`. Each
    position dict has `fen`, `played_move`, `best_move`, `classification`,
    `game_id`, `move_number`, `side` -- the last three identify the puzzle
    back to the server for `POST /practice/check-move` attempt tracking.
    `skipped_count` counts flagged moves whose FEN reconstruction failed
    and were excluded, so callers can surface that instead of the
    position count just looking mysteriously short.
    """
    games_by_id = {game.id: game for game in games}

    moves_by_pattern = aggregated_data.get("moves_by_pattern")
    if not moves_by_pattern:
        top_pattern = aggregated_data.get("top_pattern")
        top_pattern_moves = aggregated_data.get("top_pattern_moves") or []
        if top_pattern_moves:
            if top_pattern:
                key = f"{top_pattern['phase']}:{top_pattern['classification']}"
            else:
                key = "top_pattern"
            moves_by_pattern = {key: top_pattern_moves}
        else:
            moves_by_pattern = {}

    # Round-robin interleave across patterns, in the patterns' ranked order.
    move_queues = [list(moves) for moves in moves_by_pattern.values()]
    interleaved: list[dict[str, Any]] = []
    while any(move_queues):
        for queue in move_queues:
            if queue:
                interleaved.append(queue.pop(0))

    if session is not None:
        game_ids = [g.id for g in games if g.id is not None]
        attempts_by_key: dict[tuple[int, int, str], PracticeAttempt] = {}
        if game_ids:
            rows = session.exec(
                select(PracticeAttempt).where(PracticeAttempt.game_id.in_(game_ids))
            ).all()
            attempts_by_key = {(row.game_id, row.move_number, row.side): row for row in rows}

        def _attempt_for(move: dict[str, Any]) -> PracticeAttempt | None:
            return attempts_by_key.get((move["game_id"], move["move_number"], move["side"]))

        def _is_solved(move: dict[str, Any]) -> bool:
            attempt = _attempt_for(move)
            return attempt is not None and attempt.solved

        def _is_open_retry(move: dict[str, Any]) -> bool:
            attempt = _attempt_for(move)
            return attempt is not None and not attempt.solved and attempt.attempts_count > 0

        interleaved = [m for m in interleaved if not _is_solved(m)]
        # Stable sort: open-retry moves move to the front, relative order
        # preserved within each group.
        interleaved.sort(key=lambda m: 0 if _is_open_retry(m) else 1)

    positions: list[dict[str, Any]] = []
    skipped_count = 0

    for move in interleaved:
        if len(positions) >= max_positions:
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
            skipped_count += 1
            continue

        positions.append(
            {
                "fen": fen,
                "played_move": move["san"],
                "best_move": move["best_move"],
                "classification": move["classification"],
                "game_id": move["game_id"],
                "move_number": move["move_number"],
                "side": move["side"],
            }
        )

    return {"positions": positions, "skipped_count": skipped_count}

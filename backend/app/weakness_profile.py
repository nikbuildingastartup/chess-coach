"""Deterministic aggregation of flagged moves across recent games into a
weakness pattern. No LLM/network calls happen here -- see `app.focus` for
the LLM-backed narrative layer built on top of this data.
"""

import json
from collections import Counter
from typing import Any

from app.models import Game

# Below this many analyzed games in the aggregation window, a cross-game
# pattern would be too speculative to surface as a confident recommendation
# -- `GET /focus/today` falls back to an "insufficient_data" status instead.
MIN_GAMES_FOR_PATTERN = 3

# Only these classifications count as "weaknesses" to aggregate here --
# "inaccuracy" and "good" are too noisy/expected to drive a daily focus.
FLAGGED_CLASSIFICATIONS = ("blunder", "mistake")

# Tie-break priority when two (phase, classification) patterns are tied on
# frequency: a blunder is worse than a mistake, so it wins the tie.
_CLASSIFICATION_PRIORITY = {"blunder": 0, "mistake": 1}


def aggregate_weakness_data(games: list[Game]) -> dict[str, Any]:
    """Aggregate flagged (blunder/mistake) moves across `games` into a
    structured weakness summary.

    Only moves on the user's own side (`entry["side"] == game.user_color`)
    are considered -- the opponent's mistakes aren't the user's weakness.
    Games without `analysis_json` or `user_color` are skipped entirely
    (defensive: by the time this is called, callers should already only be
    passing `analyzed == True` games, but this keeps the function safe to
    call with any `Game` list).

    Returns a dict with:
      - "total_games": number of games considered (`len(games)`).
      - "total_flagged": total blunder/mistake count across all games.
      - "counts_by_pattern": `{"<phase>:<classification>": count}`.
      - "top_pattern": `{"phase", "classification", "count"}` for the most
        frequent (phase, classification) combination, or `None` if no
        flagged moves were found at all. Ties are broken by preferring
        "blunder" over "mistake", then by whichever pattern's most recent
        occurrence is more recent.
      - "top_pattern_moves": every flagged move matching `top_pattern`,
        sorted newest game first -- each a dict with `game_id`, `end_time`
        (ISO string), `move_number`, `san`, `side`, `classification`,
        `phase`, `eval_cp`, `best_move`.
      - "affected_game_ids": sorted list of game ids with at least one
        flagged move (any pattern, not just the top one).
    """
    counts: Counter[tuple[str, str]] = Counter()
    all_flagged: list[dict[str, Any]] = []

    for game in games:
        if not game.analysis_json or not game.user_color:
            continue
        entries = json.loads(game.analysis_json)
        for entry in entries:
            if entry.get("side") != game.user_color:
                continue
            classification = entry.get("classification")
            if classification not in FLAGGED_CLASSIFICATIONS:
                continue
            phase = entry.get("phase")
            counts[(phase, classification)] += 1
            all_flagged.append(
                {
                    "game_id": game.id,
                    "end_time": game.end_time.isoformat() if game.end_time else None,
                    "move_number": entry.get("move_number"),
                    "san": entry.get("san"),
                    "side": entry.get("side"),
                    "classification": classification,
                    "phase": phase,
                    "eval_cp": entry.get("eval_cp"),
                    "best_move": entry.get("best_move"),
                }
            )

    affected_game_ids = sorted({m["game_id"] for m in all_flagged if m["game_id"] is not None})

    if not counts:
        return {
            "total_games": len(games),
            "total_flagged": 0,
            "counts_by_pattern": {},
            "top_pattern": None,
            "top_pattern_moves": [],
            "affected_game_ids": [],
        }

    top_phase, top_classification, top_count = _pick_top_pattern(counts, all_flagged)

    top_pattern_moves = [
        m
        for m in all_flagged
        if m["phase"] == top_phase and m["classification"] == top_classification
    ]
    # Newest game first (empty/None end_time sorts last).
    top_pattern_moves.sort(key=lambda m: m["end_time"] or "", reverse=True)

    counts_by_pattern = {
        f"{phase}:{classification}": count for (phase, classification), count in counts.items()
    }

    return {
        "total_games": len(games),
        "total_flagged": len(all_flagged),
        "counts_by_pattern": counts_by_pattern,
        "top_pattern": {
            "phase": top_phase,
            "classification": top_classification,
            "count": top_count,
        },
        "top_pattern_moves": top_pattern_moves,
        "affected_game_ids": affected_game_ids,
    }


def _pick_top_pattern(
    counts: Counter[tuple[str, str]], all_flagged: list[dict[str, Any]]
) -> tuple[str, str, int]:
    """Pick the single most frequent (phase, classification) pattern,
    breaking ties deterministically (blunder over mistake, then most-recent
    occurrence) since `Counter.most_common` ties on insertion order only."""
    max_count = max(counts.values())
    candidates = [pc for pc, count in counts.items() if count == max_count]

    def _latest_end_time(pattern: tuple[str, str]) -> str:
        phase, classification = pattern
        times = [
            m["end_time"]
            for m in all_flagged
            if m["phase"] == phase and m["classification"] == classification and m["end_time"]
        ]
        return max(times) if times else ""

    # Two stable sorts: recency (descending) applied first as the
    # secondary key, then classification priority (ascending) applied last
    # as the primary key -- stable sort preserves the recency ordering
    # within each priority group.
    candidates.sort(key=_latest_end_time, reverse=True)
    candidates.sort(key=lambda pc: _CLASSIFICATION_PRIORITY.get(pc[1], 99))

    top_phase, top_classification = candidates[0]
    return top_phase, top_classification, max_count

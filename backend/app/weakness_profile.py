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

# How many distinct (phase, classification) patterns to surface for
# practice puzzle generation -- more than a handful stops being "your top
# weaknesses" and starts being "every mistake you've ever made".
MAX_TOP_PATTERNS = 3

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
      - "top_patterns": list of up to `MAX_TOP_PATTERNS` `{"phase",
        "classification", "count"}` dicts, ranked same as `top_pattern`.
      - "moves_by_pattern": `{"<phase>:<classification>": [moves]}` dict
        covering each pattern in `top_patterns`, with moves sorted newest
        game first.
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
            "top_patterns": [],
            "moves_by_pattern": {},
            "affected_game_ids": [],
        }

    top_patterns_ranked = _pick_top_patterns(counts, all_flagged, n=MAX_TOP_PATTERNS)
    top_phase, top_classification, top_count = top_patterns_ranked[0]

    def _moves_for(phase: str, classification: str) -> list[dict[str, Any]]:
        moves = [
            m for m in all_flagged if m["phase"] == phase and m["classification"] == classification
        ]
        # Newest game first (empty/None end_time sorts last).
        moves.sort(key=lambda m: m["end_time"] or "", reverse=True)
        return moves

    top_pattern_moves = _moves_for(top_phase, top_classification)

    moves_by_pattern = {
        f"{phase}:{classification}": _moves_for(phase, classification)
        for phase, classification, _count in top_patterns_ranked
    }

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
        "top_patterns": [
            {"phase": phase, "classification": classification, "count": count}
            for phase, classification, count in top_patterns_ranked
        ],
        "moves_by_pattern": moves_by_pattern,
        "affected_game_ids": affected_game_ids,
    }


def _pick_top_patterns(
    counts: Counter[tuple[str, str]], all_flagged: list[dict[str, Any]], n: int = 1
) -> list[tuple[str, str, int]]:
    """Rank (phase, classification) patterns by frequency, breaking ties
    deterministically (blunder over mistake, then most-recent occurrence)
    since `Counter.most_common` ties on insertion order only.

    Returns up to `n` `(phase, classification, count)` tuples, highest
    count first. With `n=1` this reproduces the old single-pattern
    behavior exactly."""

    def _latest_end_time(pattern: tuple[str, str]) -> str:
        phase, classification = pattern
        times = [
            m["end_time"]
            for m in all_flagged
            if m["phase"] == phase and m["classification"] == classification and m["end_time"]
        ]
        return max(times) if times else ""

    # Three stable sorts, least-important key first: recency (tertiary),
    # classification priority (secondary), count descending (primary).
    # Stable sort means each later sort only breaks ties within groups the
    # earlier sort already ordered.
    ranked = list(counts.items())
    ranked.sort(key=lambda item: _latest_end_time(item[0]), reverse=True)
    ranked.sort(key=lambda item: _CLASSIFICATION_PRIORITY.get(item[0][1], 99))
    ranked.sort(key=lambda item: item[1], reverse=True)

    return [(phase, classification, count) for (phase, classification), count in ranked[:n]]

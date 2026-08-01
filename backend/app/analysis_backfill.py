"""Batch re-analysis of recently-ended games that haven't been analyzed yet.

Stockfish analysis is slow (one full engine pass per game), so this is
deliberately bounded to a small batch per run rather than analyzing
everything unanalyzed in one go.
"""

import json
import logging
from collections.abc import Callable

from sqlmodel import Session, select

from app.chess_engine import analyze_game
from app.models import Game

logger = logging.getLogger(__name__)

# How many of the most-recently-ended unanalyzed games to consider per call.
# Deliberately small: Stockfish analysis is slow and this is meant to run as
# a bounded batch job, not backfill an entire game history at once.
BACKFILL_LIMIT = 10


def backfill_recent_games(
    session: Session,
    limit: int = BACKFILL_LIMIT,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Game]:
    """Analyze the most recently-ended not-yet-analyzed games.

    Selects up to `limit` games with `analyzed == False`, most recent
    `end_time` first, across all `source` values. Each selected game is run
    through `analyze_game` and the DB session is committed after every
    individual game so a crash or timeout partway through a batch doesn't
    lose progress on games already analyzed (same philosophy as the
    commit-per-archive-month pattern in `sync_games`).

    Games with `source == "chesscom"` and `user_color is None` are skipped
    (and logged, not treated as an error): those were imported before the
    `user_color` field existed and haven't been re-synced since, so there's
    no way yet to tell the user's moves from the opponent's -- analyzing
    them would be wasted work for the weakness-profile aggregation that
    consumes this data later. `source == "played"` games always have
    `user_color` set already, so they're never skipped for this reason.

    Args:
        session: DB session to query and commit against.
        limit: Maximum number of candidate games to consider (not the
            number guaranteed to be analyzed -- skipped games still count
            against this limit, since they were among the most recent
            unanalyzed candidates).
        on_progress: Optional callback invoked with `(games_done_so_far,
            total_candidates)`. Called once up front with `(0,
            total_candidates)` before any analysis starts, then again after
            each individual analyzed game's commit, with the running count
            of games analyzed so far. `total_candidates` is the number of
            candidate rows selected (i.e. `len(candidates)`, which may be
            less than `limit`), not the number that will actually be
            analyzed -- skipped games (see above) don't trigger an
            additional call, since they never commit. Not called at all
            when left as the default `None`.

    Returns:
        The games that were actually analyzed (in the order they were
        processed), for a caller (e.g. a later weakness-profile step) to
        act on immediately without re-querying.
    """
    candidates = session.exec(
        select(Game).where(Game.analyzed == False).order_by(Game.end_time.desc()).limit(limit)  # noqa: E712
    ).all()

    total_candidates = len(candidates)
    if on_progress is not None:
        on_progress(0, total_candidates)

    analyzed_games: list[Game] = []

    for game in candidates:
        if game.source == "chesscom" and game.user_color is None:
            logger.info(
                "Skipping backfill analysis for game id=%s: source is "
                "'chesscom' but user_color is None (not re-synced since "
                "the user_color feature was introduced).",
                game.id,
            )
            continue

        analysis = analyze_game(game.pgn)
        game.analysis_json = json.dumps(analysis)
        game.analyzed = True
        session.add(game)
        session.commit()
        session.refresh(game)
        analyzed_games.append(game)

        if on_progress is not None:
            on_progress(len(analyzed_games), total_candidates)

    return analyzed_games

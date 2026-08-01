"""Batch re-analysis of recently-ended games that haven't been analyzed yet.

Stockfish analysis is slow (one full engine pass per game), so this is
deliberately bounded to a small batch per run rather than analyzing
everything unanalyzed in one go.
"""

import json
import logging

from sqlmodel import Session, select

from app.chess_engine import analyze_game
from app.models import Game

logger = logging.getLogger(__name__)

# How many of the most-recently-ended unanalyzed games to consider per call.
# Deliberately small: Stockfish analysis is slow and this is meant to run as
# a bounded batch job, not backfill an entire game history at once.
BACKFILL_LIMIT = 10


def backfill_recent_games(session: Session, limit: int = BACKFILL_LIMIT) -> list[Game]:
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

    Returns:
        The games that were actually analyzed (in the order they were
        processed), for a caller (e.g. a later weakness-profile step) to
        act on immediately without re-querying.
    """
    candidates = session.exec(
        select(Game).where(Game.analyzed == False).order_by(Game.end_time.desc()).limit(limit)  # noqa: E712
    ).all()

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

    return analyzed_games

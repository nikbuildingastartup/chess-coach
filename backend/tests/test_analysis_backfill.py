import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis_backfill import backfill_recent_games
from app.chess_engine import analyze_game as real_analyze_game
from app.models import Game

# Short, fast-to-analyze PGN (mirrors the sample used in test_chess_engine.py)
# so these tests actually invoke Stockfish without being slow.
SHORT_PGN = "1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6"

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _game(
    *,
    end_time: datetime,
    source: str = "chesscom",
    user_color: str | None = "white",
    analyzed: bool = False,
    pgn: str = SHORT_PGN,
    chesscom_game_id: str | None = None,
) -> Game:
    return Game(
        chesscom_game_id=chesscom_game_id,
        pgn=pgn,
        end_time=end_time,
        time_class="blitz",
        result="win",
        source=source,
        analyzed=analyzed,
        user_color=user_color,
    )


def test_backfill_analyzes_and_persists_an_unanalyzed_game():
    session = _make_session()
    game = _game(end_time=BASE_TIME, chesscom_game_id="g1")
    session.add(game)
    session.commit()

    result = backfill_recent_games(session)

    assert len(result) == 1
    analyzed_game = result[0]
    assert analyzed_game.analyzed is True
    assert analyzed_game.analysis_json is not None
    stored_analysis = json.loads(analyzed_game.analysis_json)
    assert len(stored_analysis) == 6  # one entry per half-move in SHORT_PGN

    # Persisted to the DB, not just mutated in memory.
    with Session(session.bind) as verify_session:
        db_game = verify_session.exec(select(Game).where(Game.id == analyzed_game.id)).one()
        assert db_game.analyzed is True
        assert db_game.analysis_json is not None


def test_backfill_skips_already_analyzed_games():
    session = _make_session()
    already_analyzed = _game(
        end_time=BASE_TIME,
        analyzed=True,
        chesscom_game_id="g1",
    )
    already_analyzed.analysis_json = "[]"
    session.add(already_analyzed)
    session.commit()

    result = backfill_recent_games(session)

    assert result == []


def test_backfill_only_analyzes_the_n_most_recent_unanalyzed_games():
    session = _make_session()
    # 3 unanalyzed games at different end_times; limit=2 should only pick
    # the two most recent.
    oldest = _game(end_time=BASE_TIME, chesscom_game_id="oldest")
    middle = _game(end_time=BASE_TIME + timedelta(days=1), chesscom_game_id="middle")
    newest = _game(end_time=BASE_TIME + timedelta(days=2), chesscom_game_id="newest")
    session.add(oldest)
    session.add(middle)
    session.add(newest)
    session.commit()

    result = backfill_recent_games(session, limit=2)

    assert {g.chesscom_game_id for g in result} == {"middle", "newest"}


def test_backfill_orders_results_newest_first():
    session = _make_session()
    older = _game(end_time=BASE_TIME, chesscom_game_id="older")
    newer = _game(end_time=BASE_TIME + timedelta(days=1), chesscom_game_id="newer")
    session.add(older)
    session.add(newer)
    session.commit()

    result = backfill_recent_games(session)

    assert [g.chesscom_game_id for g in result] == ["newer", "older"]


def test_backfill_skips_chesscom_games_missing_user_color():
    session = _make_session()
    missing_color = _game(
        end_time=BASE_TIME,
        source="chesscom",
        user_color=None,
        chesscom_game_id="no-color",
    )
    session.add(missing_color)
    session.commit()

    result = backfill_recent_games(session)

    assert result == []
    with Session(session.bind) as verify_session:
        db_game = verify_session.exec(select(Game)).one()
        # Skipped, not treated as an error: still unanalyzed, untouched.
        assert db_game.analyzed is False
        assert db_game.analysis_json is None


def test_backfill_does_not_skip_played_games_even_without_chesscom_color_path():
    """`source == "played"` games always have user_color set (from Task 1),
    but this test confirms the skip condition is specifically scoped to
    `source == "chesscom"` and wouldn't accidentally skip a played game
    even if its user_color were somehow None."""
    session = _make_session()
    played = _game(
        end_time=BASE_TIME,
        source="played",
        user_color="white",
        chesscom_game_id=None,
    )
    session.add(played)
    session.commit()

    result = backfill_recent_games(session)

    assert len(result) == 1
    assert result[0].source == "played"


def test_backfill_persists_progress_after_each_game_individually():
    """If a later game in the batch blows up, games already analyzed
    earlier in the same call must have already been committed -- not lost
    because the whole batch shares one uncommitted transaction. Mirrors
    test_sync_commits_earlier_months_when_a_later_month_fails in
    test_games.py: force the second of three games to fail mid-analysis and
    confirm the first game's analysis survives in the DB despite the
    exception propagating out of backfill_recent_games."""
    session = _make_session()
    first = _game(end_time=BASE_TIME + timedelta(days=2), chesscom_game_id="first")
    second = _game(end_time=BASE_TIME + timedelta(days=1), chesscom_game_id="second")
    third = _game(end_time=BASE_TIME, chesscom_game_id="third")
    session.add(first)
    session.add(second)
    session.add(third)
    session.commit()

    call_count = 0

    def flaky_analyze_game(pgn: str):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated crash during analysis")
        return real_analyze_game(pgn)

    with patch("app.analysis_backfill.analyze_game", side_effect=flaky_analyze_game):
        with pytest.raises(RuntimeError):
            backfill_recent_games(session)

    # "first" (processed before the simulated crash) must be committed and
    # analyzed; "second" and "third" (crash + never reached) must not be.
    with Session(session.bind) as verify_session:
        games = {
            g.chesscom_game_id: g for g in verify_session.exec(select(Game)).all()
        }
        assert games["first"].analyzed is True
        assert games["first"].analysis_json is not None
        assert games["second"].analyzed is False
        assert games["third"].analyzed is False


def test_backfill_returns_empty_list_when_nothing_to_analyze():
    session = _make_session()
    result = backfill_recent_games(session)
    assert result == []

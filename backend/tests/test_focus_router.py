import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import DailyFocus, Game

AUTH_HEADERS = {"Authorization": f"Bearer {settings.app_secret}"}

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Short, already-classified PGN -- since these games are pre-seeded with
# analyzed=True, `backfill_recent_games` (which calls the real, slow
# Stockfish-backed `analyze_game`) never has anything to do here, so these
# tests don't need to mock Stockfish at all.
SHORT_PGN = "1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6"

BLUNDER_ANALYSIS = [
    {
        "move_number": 1,
        "san": "e4",
        "side": "white",
        "classification": "good",
        "phase": "opening",
        "eval_cp": 20,
        "best_move": None,
    },
    {
        "move_number": 3,
        "san": "Qxf6",
        "side": "white",
        "classification": "blunder",
        "phase": "opening",
        "eval_cp": -900,
        "best_move": "Nf3",
    },
]


def _seed_analyzed_game(session: Session, *, game_id_suffix: str, end_time: datetime) -> Game:
    game = Game(
        chesscom_game_id=f"g-{game_id_suffix}",
        pgn=SHORT_PGN,
        end_time=end_time,
        time_class="blitz",
        result="win",
        source="chesscom",
        analyzed=True,
        analysis_json=json.dumps(BLUNDER_ANALYSIS),
        user_color="white",
    )
    session.add(game)
    session.commit()
    session.refresh(game)
    return game


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_client(db_engine):
    """Client backed by an isolated in-memory DB.

    The `GET /focus/today` background task opens its own session via
    `app.db.engine` directly (BackgroundTasks run after the request-scoped
    session has closed) rather than through the `get_session` dependency,
    so both the FastAPI dependency override AND `app.routers.focus.engine`
    must point at the same test engine for a test to see consistent state.
    Starlette's `TestClient` runs `BackgroundTasks` synchronously as part
    of the same request/response cycle, so by the time `client.get(...)`
    returns, the background computation has already finished -- no polling
    needed in these tests.
    """

    def get_session_override():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    with patch("app.routers.focus.engine", db_engine):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def test_focus_today_returns_insufficient_data_below_min_games(db_client, db_engine):
    with Session(db_engine) as session:
        _seed_analyzed_game(session, game_id_suffix="1", end_time=BASE_TIME)

    response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "computing"

    with Session(db_engine) as session:
        focus = session.exec(select(DailyFocus)).one()
        assert focus.status == "insufficient_data"


def test_focus_today_computes_ready_focus_with_practice_positions(db_client, db_engine):
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(
                session, game_id_suffix=str(i), end_time=BASE_TIME + timedelta(days=i)
            )

    with patch(
        "app.routers.focus.generate_daily_focus",
        return_value={
            "headline": "Opening blunders",
            "explanation": "You keep hanging pieces early.",
            "recommendation": "Slow down in the opening.",
        },
    ):
        db_client.get("/focus/today", headers=AUTH_HEADERS)

        # Idempotent second call -- must return the same computed entry
        # without recomputing (no new DailyFocus row, no new generate call).
        second_response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert second_response.status_code == 200
    body = second_response.json()
    assert body["status"] == "ready"
    assert body["headline"] == "Opening blunders"
    assert body["explanation"] == "You keep hanging pieces early."
    assert body["recommendation"] == "Slow down in the opening."
    assert len(body["practice_positions"]) >= 1
    position = body["practice_positions"][0]
    assert position["played_move"] == "Qxf6"
    assert position["best_move"] == "Nf3"
    assert position["classification"] == "blunder"
    assert "fen" in position

    with Session(db_engine) as session:
        rows = session.exec(select(DailyFocus)).all()
        assert len(rows) == 1  # only one row for today, despite two GET calls


def test_focus_today_with_no_games_at_all_returns_insufficient_data(db_client, db_engine):
    response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "computing"

    with Session(db_engine) as session:
        focus = session.exec(select(DailyFocus)).one()
        assert focus.status == "insufficient_data"


def test_focus_today_falls_back_to_stats_text_without_api_key(db_client, db_engine):
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(
                session, game_id_suffix=str(i), end_time=BASE_TIME + timedelta(days=i)
            )

    with patch("app.focus.settings") as mock_settings:
        mock_settings.fal_api_key = None
        response = db_client.get("/focus/today", headers=AUTH_HEADERS)
        second = db_client.get("/focus/today", headers=AUTH_HEADERS)

    body = second.json()
    assert body["status"] == "ready"
    assert body["headline"] is not None
    assert "opening" in body["explanation"]
    assert response.status_code == 200


def test_focus_today_without_auth_returns_401(db_client):
    response = db_client.get("/focus/today")
    assert response.status_code == 401


def test_focus_today_marks_error_status_when_computation_raises(db_client, db_engine):
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(
                session, game_id_suffix=str(i), end_time=BASE_TIME + timedelta(days=i)
            )

    with patch("app.routers.focus.aggregate_weakness_data", side_effect=RuntimeError("boom")):
        db_client.get("/focus/today", headers=AUTH_HEADERS)

    with Session(db_engine) as session:
        focus = session.exec(select(DailyFocus)).one()
        assert focus.status == "error"


def test_focus_today_marks_error_status_when_db_commit_fails_mid_computation(
    db_client, db_engine
):
    """Regression test for a missing `session.rollback()` in the except
    block of `_compute_daily_focus`.

    A DB-level failure (e.g. an IntegrityError from a commit inside
    `backfill_recent_games`, which commits per-game within the same
    session -- see `app/analysis_backfill.py`) leaves the SQLAlchemy
    session in a state that requires an explicit `rollback()` before it
    can be reused. Without that rollback, the except block's own
    `session.get(DailyFocus, focus_id)` call raises too, which is caught
    by the inner `except Exception` and silently logged -- leaving the
    `DailyFocus` row stuck at `status="computing"` forever instead of
    being marked `"error"`.

    This is simulated here by having `backfill_recent_games` itself
    trigger a real unique-constraint violation on commit, which is
    exactly the kind of DB-level failure the original bug couldn't
    recover from.
    """
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(
                session, game_id_suffix=str(i), end_time=BASE_TIME + timedelta(days=i)
            )

    def _raise_via_dirty_commit(session: Session) -> None:
        # Duplicate chesscom_game_id -- violates the unique constraint on
        # Game.chesscom_game_id, so this commit raises IntegrityError and
        # leaves `session` needing an explicit rollback() before reuse,
        # just like a real failed commit inside `backfill_recent_games`
        # would.
        session.add(
            Game(
                chesscom_game_id="g-0",
                pgn=SHORT_PGN,
                end_time=BASE_TIME,
                time_class="blitz",
                result="win",
                source="chesscom",
            )
        )
        session.commit()

    with patch(
        "app.routers.focus.backfill_recent_games", side_effect=_raise_via_dirty_commit
    ):
        db_client.get("/focus/today", headers=AUTH_HEADERS)

    with Session(db_engine) as session:
        focus = session.exec(select(DailyFocus)).one()
        assert focus.status == "error"

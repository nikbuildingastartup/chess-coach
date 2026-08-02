import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
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


def test_focus_today_computes_ready_focus(db_client, db_engine):
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

    with Session(db_engine) as session:
        rows = session.exec(select(DailyFocus)).all()
        assert len(rows) == 1  # only one row for today, despite two GET calls


def test_focus_today_ready_row_with_legacy_practice_positions_json_does_not_500(
    db_client, db_engine
):
    """Regression test: a `DailyFocus` row persisted by an older version of
    this code stored `practice_positions_json` as a list of dicts with only
    `fen`/`played_move`/`best_move`/`classification` -- no `game_id`/
    `move_number`/`side` (those fields were added later, and `_to_response`
    used to eagerly parse this JSON into `PracticePosition` models, which
    require them). `GET /focus/today` must not 500 when it encounters such
    a row; the fix is to stop parsing `practice_positions_json` in the
    response path entirely."""
    legacy_positions = [
        {
            "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            "played_move": "Qxf6",
            "best_move": "Nf3",
            "classification": "blunder",
        }
    ]
    with Session(db_engine) as session:
        focus = DailyFocus(
            date=datetime.now(timezone.utc).date().isoformat(),
            status="ready",
            headline="Old headline",
            explanation="Old explanation",
            recommendation="Old recommendation",
            practice_positions_json=json.dumps(legacy_positions),
            created_at=datetime.now(timezone.utc),
        )
        session.add(focus)
        session.commit()

    response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["headline"] == "Old headline"


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


def test_focus_today_retries_when_existing_status_is_insufficient_data(db_client, db_engine):
    """Regression test: a cached `insufficient_data` row must not be
    returned as-is for the rest of the day -- if the user has since played
    enough games, the next request should re-trigger computation and reuse
    the same row (not insert a second one) rather than being stuck showing
    stale `insufficient_data` until the next UTC date."""
    today = datetime.now(timezone.utc).date().isoformat()
    with Session(db_engine) as session:
        session.add(
            DailyFocus(date=today, status="insufficient_data", created_at=datetime.now(timezone.utc))
        )
        session.commit()
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
        response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    # The response body reflects state right after the reset-to-"computing"
    # commit, before the (synchronously-run-by-TestClient) background task
    # updates it further -- same pattern as the other "computing" assertions
    # in this file.
    assert response.json()["status"] == "computing"

    with Session(db_engine) as session:
        rows = session.exec(select(DailyFocus)).all()
        assert len(rows) == 1  # reused the existing row, didn't insert a second one
        assert rows[0].status == "ready"
        assert rows[0].headline == "Opening blunders"


def test_focus_today_retries_when_existing_status_is_error(db_client, db_engine):
    """Same as the `insufficient_data` retry case, but for a stale `error`
    row -- a transient failure shouldn't lock the user out of the feature
    for the rest of the day."""
    today = datetime.now(timezone.utc).date().isoformat()
    with Session(db_engine) as session:
        session.add(DailyFocus(date=today, status="error", created_at=datetime.now(timezone.utc)))
        session.commit()
        for i in range(3):
            _seed_analyzed_game(
                session, game_id_suffix=str(i), end_time=BASE_TIME + timedelta(days=i)
            )

    response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "computing"

    with Session(db_engine) as session:
        rows = session.exec(select(DailyFocus)).all()
        assert len(rows) == 1
        assert rows[0].status == "ready"


def test_focus_today_does_not_retry_when_existing_status_is_ready_or_computing(
    db_client, db_engine
):
    """`ready` and `computing` are the only two statuses that should
    short-circuit to an immediate return without dispatching new work."""
    today = datetime.now(timezone.utc).date().isoformat()
    with Session(db_engine) as session:
        session.add(
            DailyFocus(
                date=today,
                status="ready",
                headline="Existing headline",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    with patch("app.routers.focus._compute_daily_focus") as mock_compute:
        response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["headline"] == "Existing headline"
    mock_compute.assert_not_called()


def test_focus_today_progress_updates_incrementally_during_backfill(db_client, db_engine):
    """`_compute_daily_focus` wires `backfill_recent_games`'s `on_progress`
    callback to update `DailyFocus.progress_current`/`progress_total` on
    the row and commit, so the row is visible mid-computation to a
    concurrent poller (not just at the very end).

    `TestClient` runs `BackgroundTasks` synchronously as part of the same
    request, so there's no real opportunity to poll mid-flight from outside
    -- instead, this patches `app.routers.focus.backfill_recent_games` with
    a fake that invokes `on_progress` at several points and, from a
    *separate* session against the same test engine (simulating a
    concurrent `GET /focus/today` poll), asserts the `DailyFocus` row
    already reflects each intermediate progress value before the fake
    returns."""
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(
                session, game_id_suffix=str(i), end_time=BASE_TIME + timedelta(days=i)
            )

    observed: list[tuple[int, int]] = []

    def fake_backfill(session, on_progress=None, **kwargs):
        assert on_progress is not None
        for current, total in [(0, 3), (1, 3), (2, 3), (3, 3)]:
            on_progress(current, total)
            with Session(db_engine) as poller_session:
                polled = poller_session.exec(select(DailyFocus)).one()
                observed.append((polled.progress_current, polled.progress_total))
        return []

    with patch("app.routers.focus.backfill_recent_games", side_effect=fake_backfill):
        response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    # Each on_progress call was already committed and visible to a
    # concurrent poller by the time the next call happened.
    assert observed == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_focus_today_resets_progress_on_insufficient_data_retry(db_client, db_engine):
    """A stale row from a previous computation (progress left at, say,
    2/2 from an earlier run) must not show leftover progress from that
    earlier run once a fresh computation is dispatched -- the retry path
    must reset both fields to 0 before recomputing starts."""
    today = datetime.now(timezone.utc).date().isoformat()
    with Session(db_engine) as session:
        session.add(
            DailyFocus(
                date=today,
                status="insufficient_data",
                progress_current=2,
                progress_total=2,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "computing"
    assert body["progress_current"] == 0
    assert body["progress_total"] == 0


def test_focus_today_response_includes_progress_fields_when_ready(db_client, db_engine):
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
        second_response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    body = second_response.json()
    assert body["status"] == "ready"
    # All 3 seeded games are pre-analyzed, so backfill_recent_games has no
    # unanalyzed candidates -- progress reflects the (0, 0) zero-candidates
    # call.
    assert body["progress_current"] == 0
    assert body["progress_total"] == 0


def test_focus_today_handles_concurrent_insert_race_without_500(db_client, db_engine):
    """Regression test for the non-atomic check-then-insert race in `GET
    /focus/today`.

    Two concurrent requests can both pass the initial `SELECT` (no row for
    today yet) and then both attempt to `INSERT` a `DailyFocus` row for the
    same unique `date`. Real thread concurrency is emulated here by
    patching `Session.commit`: the first time our request's session tries
    to commit its new row, a second, independent session sneaks in and
    commits a competing row for today FIRST, so when our original commit
    then proceeds it collides with the unique constraint on `date` and
    raises `IntegrityError` -- exactly like a genuine race would. The
    route must catch that, roll back, and return the row the other
    request won the race to create, instead of propagating a 500.
    """
    original_commit = Session.commit
    injected = {"done": False}

    def racing_commit(self, *args, **kwargs):
        if not injected["done"]:
            injected["done"] = True
            with Session(db_engine) as other:
                other.add(
                    DailyFocus(
                        date=datetime.now(timezone.utc).date().isoformat(),
                        status="computing",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                other.commit()
        return original_commit(self, *args, **kwargs)

    with patch.object(Session, "commit", racing_commit):
        response = db_client.get("/focus/today", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "computing"

    with Session(db_engine) as session:
        rows = session.exec(select(DailyFocus)).all()
        assert len(rows) == 1  # no duplicate row, and no unhandled 500

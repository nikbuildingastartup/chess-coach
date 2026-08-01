import json
from datetime import datetime, timedelta, timezone

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Game, PracticeAttempt

AUTH_HEADERS = {"Authorization": f"Bearer {settings.app_secret}"}

STARTING_FEN = chess.STARTING_FEN


def _fen_after(sans: list[str]) -> str:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.fen()


# Position right before White's 3rd move, after 1.e4 Nf6 2.Qf3 Nc6. Qxf6
# (f3f6) hangs the queen for a knight -- a predictable, large blunder.
BEFORE_BLUNDER_FEN = _fen_after(["e4", "Nf6", "Qf3", "Nc6"])

# Position right before Black's recapture, after 1.e4 Nf6 2.Qf3 Nc6 3.Qxf6.
# Both gxf6 and exf6 recapture the hanging queen for free and are similarly
# strong -- whichever one Stockfish doesn't name as its top choice is still
# a "close enough" move, not a genuine mistake.
BEFORE_RECAPTURE_FEN = _fen_after(["e4", "Nf6", "Qf3", "Nc6", "Qxf6"])

# A bare-bones position with a hanging, undefended black rook on d6 that two
# different white rooks can each capture -- one along the d-file (d1d6), one
# along the 6th rank (a6d6). With almost no material left on the board this
# is a trivially winning king+rook endgame either way, so the two captures'
# evaluations are essentially identical (a hair apart, not exactly equal) --
# a much smaller and more stable gap than the queen-recapture position
# above, useful for testing the *tolerance* path without brushing up against
# it.
TWO_ROOKS_CAN_CAPTURE_FEN = "4k3/8/R2r4/8/8/8/8/3RK3 w - - 0 1"


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

BLUNDER_ANALYSIS = [
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


def _seed_analyzed_game(session: Session, *, suffix: str, end_time: datetime) -> Game:
    game = Game(
        chesscom_game_id=f"g-{suffix}",
        pgn="1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6",
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
    def get_session_override():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_check_move_without_auth_returns_401(client):
    response = client.post(
        "/practice/check-move",
        json={"fen": STARTING_FEN, "move_uci": "e2e4"},
    )
    assert response.status_code == 401


def test_check_move_illegal_move_returns_400(client):
    response = client.post(
        "/practice/check-move",
        json={"fen": STARTING_FEN, "move_uci": "e2e5"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400


def test_check_move_malformed_uci_returns_400(client):
    response = client.post(
        "/practice/check-move",
        json={"fen": STARTING_FEN, "move_uci": "not-a-move"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400


def test_check_move_incorrect_move_flags_the_hung_queen_as_wrong(client):
    # Qxf6 hangs the queen for a knight -- a large eval swing, and almost
    # certainly not Stockfish's top choice from this position.
    response = client.post(
        "/practice/check-move",
        json={"fen": BEFORE_BLUNDER_FEN, "move_uci": "f3f6"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is False
    assert body["best_move"] != "f3f6"
    assert isinstance(body["played_eval_cp"], int)


def test_check_move_exact_match_is_correct(client):
    # First, discover Stockfish's best move in a position by submitting an
    # arbitrary legal move and reading back what the engine names as best.
    discovery = client.post(
        "/practice/check-move",
        json={"fen": BEFORE_RECAPTURE_FEN, "move_uci": "g7f6"},
        headers=AUTH_HEADERS,
    )
    assert discovery.status_code == 200
    best_move = discovery.json()["best_move"]
    assert best_move is not None

    # Now play exactly that best move and confirm it's reported correct via
    # an exact match (not just within tolerance).
    response = client.post(
        "/practice/check-move",
        json={"fen": BEFORE_RECAPTURE_FEN, "move_uci": best_move},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is True
    assert body["best_move"] == best_move


def test_check_move_close_but_not_exact_is_correct_via_tolerance(client):
    # Either rook capture (d1d6 or a6d6) wins the hanging black rook and
    # leaves a trivially winning king+rook endgame. Discover which one
    # Stockfish names as best, then play the *other* capture -- it shouldn't
    # match exactly, but should still be marked correct because the eval
    # drop between the two is tiny.
    discovery = client.post(
        "/practice/check-move",
        json={"fen": TWO_ROOKS_CAN_CAPTURE_FEN, "move_uci": "d1d6"},
        headers=AUTH_HEADERS,
    )
    assert discovery.status_code == 200
    best_move = discovery.json()["best_move"]

    other_capture = "a6d6" if best_move == "d1d6" else "d1d6"

    response = client.post(
        "/practice/check-move",
        json={"fen": TWO_ROOKS_CAN_CAPTURE_FEN, "move_uci": other_capture},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["best_move"] == best_move
    assert other_capture != best_move
    assert body["correct"] is True


def test_get_practice_positions_returns_positions_from_recent_games(db_client, db_engine):
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(session, suffix=str(i), end_time=BASE_TIME + timedelta(days=i))

    response = db_client.get("/practice/positions", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert len(body["positions"]) >= 1
    position = body["positions"][0]
    assert position["classification"] == "blunder"
    assert position["game_id"] is not None
    assert position["move_number"] == 3
    assert position["side"] == "white"
    assert body["skipped_count"] == 0
    assert body["total_tracked"] == 0
    assert body["solved_count"] == 0


def test_get_practice_positions_without_auth_returns_401(db_client):
    response = db_client.get("/practice/positions")
    assert response.status_code == 401


def test_check_move_records_attempt_when_position_identity_given(db_client, db_engine):
    with Session(db_engine) as session:
        game = _seed_analyzed_game(session, suffix="1", end_time=BASE_TIME)
        game_id = game.id

    response = db_client.post(
        "/practice/check-move",
        json={
            "fen": BEFORE_BLUNDER_FEN,
            "move_uci": "f3f6",
            "game_id": game_id,
            "move_number": 3,
            "side": "white",
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["correct"] is False

    with Session(db_engine) as session:
        attempt = session.exec(select(PracticeAttempt)).one()
        assert attempt.game_id == game_id
        assert attempt.move_number == 3
        assert attempt.side == "white"
        assert attempt.solved is False
        assert attempt.attempts_count == 1
        assert attempt.last_attempted_at is not None


def test_check_move_marks_attempt_solved_once_any_attempt_is_correct(db_client, db_engine):
    with Session(db_engine) as session:
        game = _seed_analyzed_game(session, suffix="1", end_time=BASE_TIME)
        game_id = game.id

    # First attempt: wrong.
    db_client.post(
        "/practice/check-move",
        json={
            "fen": BEFORE_BLUNDER_FEN,
            "move_uci": "f3f6",
            "game_id": game_id,
            "move_number": 3,
            "side": "white",
        },
        headers=AUTH_HEADERS,
    )

    # Second attempt: discover and play the actual best move.
    discovery = db_client.post(
        "/practice/check-move",
        json={"fen": BEFORE_BLUNDER_FEN, "move_uci": "f3f6"},
        headers=AUTH_HEADERS,
    ).json()
    best_move = discovery["best_move"]

    db_client.post(
        "/practice/check-move",
        json={
            "fen": BEFORE_BLUNDER_FEN,
            "move_uci": best_move,
            "game_id": game_id,
            "move_number": 3,
            "side": "white",
        },
        headers=AUTH_HEADERS,
    )

    with Session(db_engine) as session:
        attempt = session.exec(
            select(PracticeAttempt).where(
                PracticeAttempt.game_id == game_id,
                PracticeAttempt.move_number == 3,
                PracticeAttempt.side == "white",
            )
        ).one()
        assert attempt.solved is True
        assert attempt.attempts_count == 2


def test_check_move_without_position_identity_does_not_create_attempt(db_client, db_engine):
    response = db_client.post(
        "/practice/check-move",
        json={"fen": STARTING_FEN, "move_uci": "e2e4"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200

    with Session(db_engine) as session:
        assert session.exec(select(PracticeAttempt)).all() == []

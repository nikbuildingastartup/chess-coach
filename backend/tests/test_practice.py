import chess
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

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
    # Both gxf6 and exf6 recapture the hanging queen for free and should be
    # similarly strong. Discover which one Stockfish names as best, then
    # play the *other* recapture -- it shouldn't match exactly, but should
    # still be marked correct because the eval drop is small.
    discovery = client.post(
        "/practice/check-move",
        json={"fen": BEFORE_RECAPTURE_FEN, "move_uci": "g7f6"},
        headers=AUTH_HEADERS,
    )
    assert discovery.status_code == 200
    best_move = discovery.json()["best_move"]

    other_recapture = "e7f6" if best_move == "g7f6" else "g7f6"

    response = client.post(
        "/practice/check-move",
        json={"fen": BEFORE_RECAPTURE_FEN, "move_uci": other_recapture},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["best_move"] == best_move
    assert other_recapture != best_move
    assert body["correct"] is True

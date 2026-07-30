import chess
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

AUTH_HEADERS = {"Authorization": f"Bearer {settings.app_secret}"}

STARTING_FEN = chess.STARTING_FEN


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_engine_move_returns_legal_san_move_for_starting_position(client):
    response = client.post(
        "/play/engine-move",
        json={"fen": STARTING_FEN, "skill": "easy"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert "move" in body

    # Verify the returned SAN move is actually legal from the starting
    # position by replaying it with python-chess.
    board = chess.Board(STARTING_FEN)
    move = board.parse_san(body["move"])
    assert move in board.legal_moves


def test_engine_move_without_auth_returns_401(client):
    response = client.post(
        "/play/engine-move",
        json={"fen": STARTING_FEN, "skill": "easy"},
    )
    assert response.status_code == 401


def test_engine_move_with_invalid_skill_returns_422(client):
    response = client.post(
        "/play/engine-move",
        json={"fen": STARTING_FEN, "skill": "impossible"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422

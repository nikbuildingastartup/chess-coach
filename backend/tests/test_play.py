from unittest.mock import patch

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Game

AUTH_HEADERS = {"Authorization": f"Bearer {settings.app_secret}"}

STARTING_FEN = chess.STARTING_FEN

# 3. Qxf6?? hangs the queen for a knight: gxf6 recaptures it for free —
# a predictable blunder for end-to-end save+analyze tests.
BLUNDERING_PGN = "1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(autouse=True)
def mock_coaching_summary():
    """Prevent any test from making a real fal.ai network call.

    `POST /play/games` always calls `generate_coaching_summary` now. The
    real backend/.env (gitignored, holds the user's live FAL_KEY) is picked
    up by `Settings` even under pytest, so without this autouse mock every
    test hitting that endpoint would hit the real fal.ai API. Individual
    tests that want to exercise the success/failure paths explicitly
    override this mock's return value / side effect.
    """
    with patch(
        "app.routers.play.generate_coaching_summary",
        return_value="Default mocked coaching summary.",
    ) as mock:
        yield mock


@pytest.fixture()
def db_client(db_engine):
    """Client backed by an isolated in-memory DB, for tests that persist
    Game rows (save-game / analysis-retrieval tests)."""

    def get_session_override():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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


def test_save_game_persists_it_with_correct_fields_and_returns_analysis(db_client, db_engine):
    response = db_client.post(
        "/play/games",
        json={"pgn": BLUNDERING_PGN, "result": "win"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["game_id"], int)
    assert len(body["analysis"]) == 6

    qxf6_entry = body["analysis"][4]
    assert qxf6_entry["san"] == "Qxf6"
    assert qxf6_entry["classification"] in ("blunder", "mistake")
    assert qxf6_entry["best_move"] is not None

    # Verify the row was actually persisted with the fields the spec
    # requires for played games, not just that the endpoint returned 200.
    with Session(db_engine) as session:
        game = session.exec(select(Game).where(Game.id == body["game_id"])).one()
        assert game.source == "played"
        assert game.chesscom_game_id is None
        assert game.time_class == "untimed"
        assert game.result == "win"
        assert game.analyzed is True
        assert game.analysis_json is not None
        assert game.coaching_summary == "Default mocked coaching summary."


def test_save_game_stores_and_returns_coaching_summary_on_success(
    db_client, db_engine, mock_coaching_summary
):
    mock_coaching_summary.return_value = "Watch out for hanging pieces after queen trades."

    response = db_client.post(
        "/play/games",
        json={"pgn": BLUNDERING_PGN, "result": "win"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["coaching_summary"] == "Watch out for hanging pieces after queen trades."

    with Session(db_engine) as session:
        game = session.exec(select(Game).where(Game.id == body["game_id"])).one()
        assert game.coaching_summary == "Watch out for hanging pieces after queen trades."


def test_save_game_succeeds_with_null_coaching_summary_when_generation_fails(
    db_client, db_engine, mock_coaching_summary
):
    # `generate_coaching_summary` itself is documented to swallow errors and
    # return None rather than raise -- verify the endpoint still succeeds
    # end-to-end and persists the game when that happens.
    mock_coaching_summary.return_value = None

    response = db_client.post(
        "/play/games",
        json={"pgn": BLUNDERING_PGN, "result": "win"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["coaching_summary"] is None
    assert len(body["analysis"]) == 6

    with Session(db_engine) as session:
        game = session.exec(select(Game).where(Game.id == body["game_id"])).one()
        assert game.coaching_summary is None
        assert game.analysis_json is not None
        assert game.analyzed is True


def test_save_game_without_auth_returns_401(client):
    response = client.post("/play/games", json={"pgn": BLUNDERING_PGN, "result": "win"})
    assert response.status_code == 401


def test_get_game_analysis_returns_saved_analysis(db_client):
    save_response = db_client.post(
        "/play/games",
        json={"pgn": BLUNDERING_PGN, "result": "loss"},
        headers=AUTH_HEADERS,
    )
    game_id = save_response.json()["game_id"]

    response = db_client.get(f"/play/games/{game_id}/analysis", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] == save_response.json()["analysis"]
    assert body["coaching_summary"] == save_response.json()["coaching_summary"]
    assert body["coaching_summary"] == "Default mocked coaching summary."


def test_get_game_analysis_without_auth_returns_401(db_client):
    save_response = db_client.post(
        "/play/games",
        json={"pgn": BLUNDERING_PGN, "result": "draw"},
        headers=AUTH_HEADERS,
    )
    game_id = save_response.json()["game_id"]

    response = db_client.get(f"/play/games/{game_id}/analysis")
    assert response.status_code == 401


def test_get_game_analysis_returns_404_for_nonexistent_game(db_client):
    response = db_client.get("/play/games/999999/analysis", headers=AUTH_HEADERS)
    assert response.status_code == 404

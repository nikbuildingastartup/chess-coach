from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.chesscom_client import ChessComUnavailableError
from app.config import settings
from app.db import get_session
from app.main import app

AUTH_HEADERS = {"Authorization": f"Bearer {settings.app_secret}"}

RAW_GAMES = [
    {
        "url": "https://www.chess.com/game/live/1",
        "pgn": "1. e4 e5",
        "end_time": 1700000000,
        "time_class": "blitz",
        "white": {"username": "tester", "result": "win"},
        "black": {"username": "opponent1", "result": "checkmated"},
    },
    {
        "url": "https://www.chess.com/game/live/2",
        "pgn": "1. d4 d5",
        "end_time": 1700010000,
        "time_class": "blitz",
        "white": {"username": "opponent2", "result": "win"},
        "black": {"username": "tester", "result": "resigned"},
    },
]


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_imports_games(mock_archives, mock_games, client):
    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = RAW_GAMES

    response = client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"imported": 2, "total": 2}


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_twice_is_idempotent(mock_archives, mock_games, client):
    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = RAW_GAMES

    first = client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
    second = client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)

    assert first.status_code == 200
    assert first.json() == {"imported": 2, "total": 2}
    assert second.status_code == 200
    assert second.json() == {"imported": 0, "total": 2}


def test_sync_without_auth_returns_401(client):
    response = client.post("/games/sync", json={"username": "tester"})
    assert response.status_code == 401


@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_returns_502_when_chesscom_unavailable(mock_archives, client):
    mock_archives.side_effect = ChessComUnavailableError("chess.com is down")

    response = client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)

    assert response.status_code == 502
    assert "detail" in response.json()


def test_get_games_without_auth_returns_401(client):
    response = client.get("/games")
    assert response.status_code == 401


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_get_games_returns_games_ordered_by_end_time_descending(mock_archives, mock_games, client):
    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = RAW_GAMES

    client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
    response = client.get("/games", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert [g["chesscom_game_id"] for g in body] == [
        "https://www.chess.com/game/live/2",
        "https://www.chess.com/game/live/1",
    ]
    assert set(body[0].keys()) == {"chesscom_game_id", "end_time", "time_class", "result"}

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.chesscom_client import ChessComUnavailableError, ChessComUserNotFoundError
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import AppSettings, Game

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


@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_returns_404_when_chesscom_user_not_found(mock_archives, client):
    mock_archives.side_effect = ChessComUserNotFoundError("no such user")

    response = client.post(
        "/games/sync", json={"username": "nosuchuser"}, headers=AUTH_HEADERS
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "No Chess.com user by that name."}


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_commits_earlier_months_when_a_later_month_fails(
    mock_archives, mock_games, client
):
    mock_archives.return_value = [
        "https://api.chess.com/pub/player/tester/games/2024/01",
        "https://api.chess.com/pub/player/tester/games/2024/02",
    ]
    mock_games.side_effect = [RAW_GAMES, ChessComUnavailableError("month 2 failed")]

    response = client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
    assert response.status_code == 502

    # Games from the first (successful) archive month must still be
    # present, since the sync commits progress per-month rather than only
    # at the very end.
    follow_up = client.get("/games", headers=AUTH_HEADERS)
    assert follow_up.status_code == 200
    ids = {g["chesscom_game_id"] for g in follow_up.json()}
    assert ids == {
        "https://www.chess.com/game/live/1",
        "https://www.chess.com/game/live/2",
    }


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_end_time_round_trips_as_utc(mock_archives, mock_games, client):
    """end_time must survive the DB round-trip as the same UTC instant.

    The Chess.com raw `end_time` is a Unix timestamp (always UTC). The API
    response must reflect that same instant, tz-aware, regardless of the
    server's local timezone.
    """
    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = [RAW_GAMES[0]]

    client.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
    response = client.get("/games", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    returned_end_time = datetime.fromisoformat(body[0]["end_time"])
    assert returned_end_time.tzinfo is not None

    expected = datetime.fromtimestamp(RAW_GAMES[0]["end_time"], tz=timezone.utc)
    assert returned_end_time.astimezone(timezone.utc) == expected


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_sets_user_color_on_newly_imported_games(mock_archives, mock_games):
    """Game 1 has `tester` as White, game 2 has `tester` as Black -- newly
    inserted rows must be tagged with the matching side."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = RAW_GAMES

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    try:
        with TestClient(app) as c:
            c.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.clear()

    with Session(engine) as session:
        games = {g.chesscom_game_id: g for g in session.exec(select(Game)).all()}
        assert games["https://www.chess.com/game/live/1"].user_color == "white"
        assert games["https://www.chess.com/game/live/2"].user_color == "black"


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_backfills_user_color_on_already_imported_games(mock_archives, mock_games):
    """A game imported before `user_color` existed (or by older code) has
    `user_color=None`. Re-syncing must backfill it in place rather than
    skipping it outright, without touching any other field."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Game(
                chesscom_game_id="https://www.chess.com/game/live/1",
                pgn="1. e4 e5",
                end_time=datetime.fromtimestamp(1700000000, tz=timezone.utc),
                time_class="blitz",
                result="win",
                source="chesscom",
                user_color=None,
            )
        )
        session.commit()

    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = [RAW_GAMES[0]]

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    try:
        with TestClient(app) as c:
            response = c.post(
                "/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS
            )
    finally:
        app.dependency_overrides.clear()

    # The pre-existing game was skipped (not re-imported)...
    assert response.json() == {"imported": 0, "total": 1}

    with Session(engine) as session:
        game = session.exec(select(Game)).one()
        # ...but its user_color got backfilled.
        assert game.user_color == "white"
        assert game.pgn == "1. e4 e5"


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_does_not_overwrite_an_already_set_user_color(mock_archives, mock_games):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Game(
                chesscom_game_id="https://www.chess.com/game/live/1",
                pgn="1. e4 e5",
                end_time=datetime.fromtimestamp(1700000000, tz=timezone.utc),
                time_class="blitz",
                result="win",
                source="chesscom",
                user_color="black",  # deliberately "wrong" to prove it's untouched
            )
        )
        session.commit()

    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = [RAW_GAMES[0]]

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    try:
        with TestClient(app) as c:
            c.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.clear()

    with Session(engine) as session:
        game = session.exec(select(Game)).one()
        assert game.user_color == "black"


@patch("app.routers.games.get_games_for_month", new_callable=AsyncMock)
@patch("app.routers.games.get_archive_urls", new_callable=AsyncMock)
def test_sync_app_settings_upsert_persists_and_updates(mock_archives, mock_games):
    """AppSettings is a singleton (id=1): first sync creates it, a later
    sync with a different username updates the same row rather than
    inserting a second one."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    mock_archives.return_value = ["https://api.chess.com/pub/player/tester/games/2024/01"]
    mock_games.return_value = RAW_GAMES

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    try:
        with TestClient(app) as c:
            c.post("/games/sync", json={"username": "tester"}, headers=AUTH_HEADERS)
            c.post("/games/sync", json={"username": "someone_else"}, headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.clear()

    with Session(engine) as session:
        all_settings = session.exec(select(AppSettings)).all()
        assert len(all_settings) == 1
        assert all_settings[0].id == 1
        assert all_settings[0].chesscom_username == "someone_else"


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


def test_get_games_excludes_played_games():
    """GET /games must scope to source == "chesscom".

    Played-vs-engine games have `chesscom_game_id=None`, which
    `GameListItem` (non-optional) can't represent -- including them here
    used to make FastAPI's response validation raise a 500 for every
    request once a single played game existed. Regression test for that:
    seed one imported game and one played game directly into the DB and
    confirm only the imported one comes back from the endpoint.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    imported = Game(
        chesscom_game_id="https://www.chess.com/game/live/99",
        pgn="1. e4 e5",
        end_time=datetime.fromtimestamp(1700000000, tz=timezone.utc),
        time_class="blitz",
        result="win",
        source="chesscom",
    )
    played = Game(
        chesscom_game_id=None,
        pgn="1. e4 e5",
        end_time=datetime.now(timezone.utc),
        time_class="untimed",
        result="loss",
        source="played",
        analysis_json="[]",
        analyzed=True,
    )
    with Session(engine) as session:
        session.add(imported)
        session.add(played)
        session.commit()

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    try:
        with TestClient(app) as c:
            response = c.get("/games", headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["chesscom_game_id"] == "https://www.chess.com/game/live/99"

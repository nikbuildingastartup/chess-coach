import httpx
import pytest
import respx

from app.chesscom_client import (
    ChessComUnavailableError,
    ChessComUserNotFoundError,
    get_archive_urls,
    get_games_for_month,
)


@respx.mock
@pytest.mark.asyncio
async def test_get_archive_urls_returns_archives_list():
    route = respx.get(
        "https://api.chess.com/pub/player/testuser/games/archives"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "archives": [
                    "https://api.chess.com/pub/player/testuser/games/2024/01",
                    "https://api.chess.com/pub/player/testuser/games/2024/02",
                ]
            },
        )
    )

    result = await get_archive_urls("testuser")

    assert route.called
    request = route.calls.last.request
    assert request.headers["User-Agent"]
    assert result == [
        "https://api.chess.com/pub/player/testuser/games/2024/01",
        "https://api.chess.com/pub/player/testuser/games/2024/02",
    ]


@respx.mock
@pytest.mark.asyncio
async def test_get_archive_urls_non_2xx_raises_unavailable_error():
    respx.get("https://api.chess.com/pub/player/testuser/games/archives").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(ChessComUnavailableError):
        await get_archive_urls("testuser")


@respx.mock
@pytest.mark.asyncio
async def test_get_archive_urls_404_raises_user_not_found_error():
    respx.get("https://api.chess.com/pub/player/nosuchuser/games/archives").mock(
        return_value=httpx.Response(404)
    )

    with pytest.raises(ChessComUserNotFoundError):
        await get_archive_urls("nosuchuser")


@respx.mock
@pytest.mark.asyncio
async def test_get_archive_urls_network_error_raises_unavailable_error():
    respx.get("https://api.chess.com/pub/player/testuser/games/archives").mock(
        side_effect=httpx.ConnectError("connection failed")
    )

    with pytest.raises(ChessComUnavailableError):
        await get_archive_urls("testuser")


@respx.mock
@pytest.mark.asyncio
async def test_get_games_for_month_returns_games_list():
    archive_url = "https://api.chess.com/pub/player/testuser/games/2024/01"
    route = respx.get(archive_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "games": [
                    {
                        "url": "https://www.chess.com/game/live/123",
                        "pgn": "1. e4 e5",
                        "end_time": 1700000000,
                        "time_class": "blitz",
                    }
                ]
            },
        )
    )

    result = await get_games_for_month(archive_url)

    assert route.called
    request = route.calls.last.request
    assert request.headers["User-Agent"]
    assert result == [
        {
            "url": "https://www.chess.com/game/live/123",
            "pgn": "1. e4 e5",
            "end_time": 1700000000,
            "time_class": "blitz",
        }
    ]


@respx.mock
@pytest.mark.asyncio
async def test_get_games_for_month_non_2xx_raises_unavailable_error():
    archive_url = "https://api.chess.com/pub/player/testuser/games/2024/01"
    respx.get(archive_url).mock(return_value=httpx.Response(404))

    with pytest.raises(ChessComUnavailableError):
        await get_games_for_month(archive_url)


@respx.mock
@pytest.mark.asyncio
async def test_get_games_for_month_network_error_raises_unavailable_error():
    archive_url = "https://api.chess.com/pub/player/testuser/games/2024/01"
    respx.get(archive_url).mock(side_effect=httpx.ConnectError("connection failed"))

    with pytest.raises(ChessComUnavailableError):
        await get_games_for_month(archive_url)

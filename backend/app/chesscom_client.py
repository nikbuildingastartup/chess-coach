import httpx

USER_AGENT = "chess-coach (contact: nik@gornation.com)"


class ChessComUnavailableError(Exception):
    """Raised when the Chess.com API cannot be reached or returns an error."""


async def get_archive_urls(username: str) -> list[str]:
    url = f"https://api.chess.com/pub/player/{username}/games/archives"
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise ChessComUnavailableError(f"Chess.com API request failed: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise ChessComUnavailableError(
            f"Chess.com API returned status {response.status_code} for {url}"
        )

    return response.json()["archives"]


async def get_games_for_month(archive_url: str) -> list[dict]:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        try:
            response = await client.get(archive_url)
        except httpx.HTTPError as exc:
            raise ChessComUnavailableError(f"Chess.com API request failed: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise ChessComUnavailableError(
            f"Chess.com API returned status {response.status_code} for {archive_url}"
        )

    return response.json()["games"]

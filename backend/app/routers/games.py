from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlmodel import Session, select

from app.auth import require_auth
from app.chesscom_client import (
    ChessComUnavailableError,
    ChessComUserNotFoundError,
    get_archive_urls,
    get_games_for_month,
)
from app.db import get_session
from app.models import Game

router = APIRouter(prefix="/games", tags=["games"], dependencies=[Depends(require_auth)])


class SyncRequest(BaseModel):
    username: str


class SyncResponse(BaseModel):
    imported: int
    total: int


class GameListItem(BaseModel):
    chesscom_game_id: str
    end_time: datetime
    time_class: str
    result: str

    @field_validator("end_time", mode="after")
    @classmethod
    def _assume_utc_if_naive(cls, value: datetime) -> datetime:
        """SQLite drops tzinfo on round-trip even with a tz-aware column,
        so a naive value here always represents a UTC instant (it was
        always written as UTC — see routers/games.py sync_games). Attach
        that offset explicitly rather than letting it be misread as local
        time by API consumers.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _derive_result(raw_game: dict, username: str) -> str:
    """Result of the game from the synced player's perspective.

    Chess.com's raw game dict has separate `white`/`black` objects, each
    with its own `result` field (e.g. "win", "checkmated", "resigned").
    We pick whichever side matches the synced username.
    """
    username_lower = username.lower()
    for side in ("white", "black"):
        player = raw_game.get(side) or {}
        if str(player.get("username", "")).lower() == username_lower:
            return player.get("result", "unknown")
    return "unknown"


@router.post("/sync", response_model=SyncResponse)
async def sync_games(body: SyncRequest, session: Session = Depends(get_session)) -> SyncResponse:
    try:
        archive_urls = await get_archive_urls(body.username)
        imported = 0
        for archive_url in archive_urls:
            raw_games = await get_games_for_month(archive_url)
            for raw_game in raw_games:
                chesscom_game_id = raw_game["url"]
                existing = session.exec(
                    select(Game).where(Game.chesscom_game_id == chesscom_game_id)
                ).first()
                if existing is not None:
                    continue
                session.add(
                    Game(
                        chesscom_game_id=chesscom_game_id,
                        pgn=raw_game["pgn"],
                        end_time=datetime.fromtimestamp(raw_game["end_time"], tz=timezone.utc),
                        time_class=raw_game["time_class"],
                        result=_derive_result(raw_game, body.username),
                        source="chesscom",
                    )
                )
                imported += 1
            # Commit after each archive month so a later failure doesn't
            # discard games already fetched from earlier months.
            session.commit()
    except ChessComUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Chess.com user by that name.",
        ) from exc
    except ChessComUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    total = len(session.exec(select(Game)).all())
    return SyncResponse(imported=imported, total=total)


@router.get("", response_model=list[GameListItem])
def list_games(session: Session = Depends(get_session)) -> list[Game]:
    return list(session.exec(select(Game).order_by(Game.end_time.desc())).all())

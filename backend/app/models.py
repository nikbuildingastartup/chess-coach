from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Game(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    chesscom_game_id: str | None = Field(default=None, unique=True, index=True)
    pgn: str
    end_time: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    time_class: str
    result: str
    source: str = "chesscom"
    analysis_json: str | None = None
    analyzed: bool = False
    coaching_summary: str | None = None
    user_color: str | None = None
    """"white" or "black" -- which side the user played. None for games
    imported before this field existed and not yet backfilled by a sync."""


class AppSettings(SQLModel, table=True):
    """Singleton settings row (always id=1) for app-wide state that isn't
    tied to a single game, e.g. the last Chess.com username synced."""

    id: int = Field(default=1, primary_key=True)
    chesscom_username: str | None = None


class DailyFocus(SQLModel, table=True):
    """One cached daily-focus computation, keyed by UTC calendar date."""

    id: int | None = Field(default=None, primary_key=True)
    date: str = Field(unique=True, index=True)
    status: str
    headline: str | None = None
    explanation: str | None = None
    recommendation: str | None = None
    source_game_ids_json: str | None = None
    practice_positions_json: str | None = None
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))

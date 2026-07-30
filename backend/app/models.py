from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Game(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    chesscom_game_id: str = Field(unique=True, index=True)
    pgn: str
    end_time: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    time_class: str
    result: str
    analyzed: bool = False

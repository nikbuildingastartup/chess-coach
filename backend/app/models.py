from datetime import datetime

from sqlmodel import Field, SQLModel


class Game(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    chesscom_game_id: str = Field(unique=True, index=True)
    pgn: str
    end_time: datetime
    time_class: str
    result: str
    analyzed: bool = False

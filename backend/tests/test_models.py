from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from app.models import Game


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_game_can_be_created_with_defaults(session):
    game = Game(
        chesscom_game_id="123",
        pgn="1. e4 e5",
        end_time=datetime(2024, 1, 1, 12, 0, 0),
        time_class="blitz",
        result="1-0",
    )
    session.add(game)
    session.commit()
    session.refresh(game)

    assert game.id is not None
    assert game.analyzed is False


def test_game_chesscom_game_id_is_unique(session):
    session.add(
        Game(
            chesscom_game_id="dup-id",
            pgn="1. e4 e5",
            end_time=datetime(2024, 1, 1, 12, 0, 0),
            time_class="blitz",
            result="1-0",
        )
    )
    session.commit()

    session.add(
        Game(
            chesscom_game_id="dup-id",
            pgn="1. d4 d5",
            end_time=datetime(2024, 1, 2, 12, 0, 0),
            time_class="blitz",
            result="0-1",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()

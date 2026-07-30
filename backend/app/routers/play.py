import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import require_auth
from app.chess_engine import analyze_game, get_engine_move
from app.db import get_session
from app.models import Game

router = APIRouter(prefix="/play", tags=["play"], dependencies=[Depends(require_auth)])


class EngineMoveRequest(BaseModel):
    fen: str
    skill: Literal["easy", "medium", "hard"]


class EngineMoveResponse(BaseModel):
    move: str


@router.post("/engine-move", response_model=EngineMoveResponse)
def engine_move(body: EngineMoveRequest) -> EngineMoveResponse:
    move = get_engine_move(body.fen, body.skill)
    return EngineMoveResponse(move=move)


class SaveGameRequest(BaseModel):
    pgn: str
    result: Literal["win", "loss", "draw"]


class SaveGameResponse(BaseModel):
    game_id: int
    analysis: list[dict[str, Any]]


class GameAnalysisResponse(BaseModel):
    analysis: list[dict[str, Any]]


@router.post("/games", response_model=SaveGameResponse)
def save_played_game(
    body: SaveGameRequest, session: Session = Depends(get_session)
) -> SaveGameResponse:
    analysis = analyze_game(body.pgn)

    game = Game(
        chesscom_game_id=None,
        pgn=body.pgn,
        end_time=datetime.now(timezone.utc),
        time_class="untimed",
        result=body.result,
        source="played",
        analysis_json=json.dumps(analysis),
        analyzed=True,
    )
    session.add(game)
    session.commit()
    session.refresh(game)

    assert game.id is not None
    return SaveGameResponse(game_id=game.id, analysis=analysis)


@router.get("/games/{game_id}/analysis", response_model=GameAnalysisResponse)
def get_game_analysis(
    game_id: int, session: Session = Depends(get_session)
) -> GameAnalysisResponse:
    game = session.get(Game, game_id)
    if game is None or game.analysis_json is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No analysis found for that game.",
        )

    return GameAnalysisResponse(analysis=json.loads(game.analysis_json))

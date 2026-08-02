import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import require_auth
from app.chess_engine import analyze_game, get_engine_move
from app.coaching import generate_coaching_summary
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


class Coaching(BaseModel):
    headline: str | None = None
    explanation: str | None = None
    recommendation: str | None = None


class SaveGameResponse(BaseModel):
    game_id: int
    analysis: list[dict[str, Any]]
    coaching: Coaching | None = None


class GameAnalysisResponse(BaseModel):
    analysis: list[dict[str, Any]]
    coaching: Coaching | None = None


@router.post("/games", response_model=SaveGameResponse)
def save_played_game(
    body: SaveGameRequest, session: Session = Depends(get_session)
) -> SaveGameResponse:
    analysis = analyze_game(body.pgn)

    # Persist the game (with its full Stockfish analysis) BEFORE calling out
    # to the coaching-summary LLM. That call is a network round-trip that
    # can hang or fail; if it happened first and the request were
    # interrupted, the just-finished game and its analysis would be lost
    # entirely instead of just missing its coaching summary.
    game = Game(
        chesscom_game_id=None,
        pgn=body.pgn,
        end_time=datetime.now(timezone.utc),
        time_class="untimed",
        result=body.result,
        source="played",
        analysis_json=json.dumps(analysis),
        analyzed=True,
        coaching_summary=None,
        user_color="white",
    )
    session.add(game)
    session.commit()
    session.refresh(game)

    if game.id is None:
        raise RuntimeError(
            "Game.id is None after insert+refresh; expected the DB to have "
            "assigned a primary key."
        )

    coaching = generate_coaching_summary(body.pgn, analysis, body.result, session)
    game.coaching_summary = json.dumps(coaching) if coaching is not None else None
    session.add(game)
    session.commit()

    return SaveGameResponse(game_id=game.id, analysis=analysis, coaching=coaching)


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

    coaching = json.loads(game.coaching_summary) if game.coaching_summary else None

    return GameAnalysisResponse(analysis=json.loads(game.analysis_json), coaching=coaching)

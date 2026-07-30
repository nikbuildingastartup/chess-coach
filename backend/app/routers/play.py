from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_auth
from app.chess_engine import get_engine_move

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

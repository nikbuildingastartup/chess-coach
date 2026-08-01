from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import require_auth
from app.chess_engine import check_move

router = APIRouter(prefix="/practice", tags=["practice"], dependencies=[Depends(require_auth)])


class CheckMoveRequest(BaseModel):
    fen: str
    move_uci: str


class CheckMoveResponse(BaseModel):
    correct: bool
    best_move: str | None
    played_eval_cp: int


@router.post("/check-move", response_model=CheckMoveResponse)
def check_move_endpoint(body: CheckMoveRequest) -> CheckMoveResponse:
    try:
        result = check_move(body.fen, body.move_uci)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return CheckMoveResponse(**result)

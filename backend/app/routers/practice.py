from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.analysis_backfill import BACKFILL_LIMIT
from app.auth import require_auth
from app.chess_engine import check_move
from app.db import get_session
from app.focus import PRACTICE_POSITIONS_MAX, extract_practice_positions
from app.models import Game, PracticeAttempt
from app.routers.focus import PracticePosition
from app.weakness_profile import aggregate_weakness_data

router = APIRouter(prefix="/practice", tags=["practice"], dependencies=[Depends(require_auth)])


class CheckMoveRequest(BaseModel):
    fen: str
    move_uci: str
    game_id: int | None = None
    move_number: int | None = None
    side: str | None = None


class CheckMoveResponse(BaseModel):
    correct: bool
    best_move: str | None
    played_eval_cp: int


class PracticePositionsResponse(BaseModel):
    positions: list[PracticePosition]
    skipped_count: int
    solved_count: int
    total_tracked: int


@router.post("/check-move", response_model=CheckMoveResponse)
def check_move_endpoint(
    body: CheckMoveRequest, session: Session = Depends(get_session)
) -> CheckMoveResponse:
    try:
        result = check_move(body.fen, body.move_uci)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if body.game_id is not None and body.move_number is not None and body.side is not None:
        _record_attempt(
            session, body.game_id, body.move_number, body.side, body.fen, result["correct"]
        )

    return CheckMoveResponse(**result)


def _record_attempt(
    session: Session, game_id: int, move_number: int, side: str, fen: str, correct: bool
) -> None:
    """Upsert the `PracticeAttempt` row for one puzzle: increment the
    attempt count, and mark it solved as soon as any attempt is correct
    (a puzzle stays solved even if a later attempt at the same position is
    wrong -- `solved` only ever moves False -> True, never back)."""
    attempt = session.exec(
        select(PracticeAttempt).where(
            PracticeAttempt.game_id == game_id,
            PracticeAttempt.move_number == move_number,
            PracticeAttempt.side == side,
        )
    ).first()
    now = datetime.now(timezone.utc)

    if attempt is None:
        attempt = PracticeAttempt(
            game_id=game_id,
            move_number=move_number,
            side=side,
            fen=fen,
            solved=correct,
            attempts_count=1,
            last_attempted_at=now,
            created_at=now,
        )
    else:
        attempt.attempts_count += 1
        attempt.solved = attempt.solved or correct
        attempt.last_attempted_at = now

    session.add(attempt)
    session.commit()


@router.get("/positions", response_model=PracticePositionsResponse)
def get_practice_positions(session: Session = Depends(get_session)) -> PracticePositionsResponse:
    """Generate a fresh practice puzzle set on demand, independent of the
    once-a-day cached `DailyFocus` text -- lets the user train more than
    once a day without waiting for a new focus computation."""
    games = session.exec(
        select(Game)
        .where(Game.analyzed == True)  # noqa: E712
        .order_by(Game.end_time.desc())
        .limit(BACKFILL_LIMIT)
    ).all()

    aggregated = aggregate_weakness_data(games)
    extraction = extract_practice_positions(
        games, aggregated, session=session, max_positions=PRACTICE_POSITIONS_MAX
    )

    total_tracked = session.exec(select(func.count()).select_from(PracticeAttempt)).one()
    solved_count = session.exec(
        select(func.count())
        .select_from(PracticeAttempt)
        .where(PracticeAttempt.solved == True)  # noqa: E712
    ).one()

    return PracticePositionsResponse(
        positions=[PracticePosition(**p) for p in extraction["positions"]],
        skipped_count=extraction["skipped_count"],
        solved_count=solved_count,
        total_tracked=total_tracked,
    )

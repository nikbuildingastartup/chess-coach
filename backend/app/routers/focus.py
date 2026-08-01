import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.analysis_backfill import BACKFILL_LIMIT, backfill_recent_games
from app.auth import require_auth
from app.db import engine, get_session
from app.focus import extract_practice_positions, generate_daily_focus
from app.models import DailyFocus, Game
from app.weakness_profile import MIN_GAMES_FOR_PATTERN, aggregate_weakness_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/focus", tags=["focus"], dependencies=[Depends(require_auth)])


class PracticePosition(BaseModel):
    fen: str
    played_move: str
    best_move: str | None
    classification: str


class FocusResponse(BaseModel):
    id: int
    date: str
    status: str
    headline: str | None = None
    explanation: str | None = None
    recommendation: str | None = None
    source_game_ids_json: str | None = None
    practice_positions_json: str | None = None
    created_at: datetime
    practice_positions: list[PracticePosition] = []


def _to_response(focus: DailyFocus) -> FocusResponse:
    if focus.id is None:
        raise RuntimeError("DailyFocus.id is None; expected a persisted row.")

    practice_positions_raw = (
        json.loads(focus.practice_positions_json) if focus.practice_positions_json else []
    )

    return FocusResponse(
        id=focus.id,
        date=focus.date,
        status=focus.status,
        headline=focus.headline,
        explanation=focus.explanation,
        recommendation=focus.recommendation,
        source_game_ids_json=focus.source_game_ids_json,
        practice_positions_json=focus.practice_positions_json,
        created_at=focus.created_at,
        practice_positions=[PracticePosition(**p) for p in practice_positions_raw],
    )


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@router.get("/today", response_model=FocusResponse)
def get_today_focus(
    background_tasks: BackgroundTasks, session: Session = Depends(get_session)
) -> FocusResponse:
    """Return today's (UTC) cached daily focus, computing it in the
    background the first time it's requested for a given date.

    If a `DailyFocus` row already exists for today (any status, including
    "computing" from an in-flight earlier request), it's returned as-is --
    the frontend is expected to poll while `status == "computing"`. This
    also makes the endpoint idempotent: it never re-triggers computation
    for a date that's already been kicked off.
    """
    today = _today_utc()
    existing = session.exec(select(DailyFocus).where(DailyFocus.date == today)).first()
    if existing is not None:
        return _to_response(existing)

    focus = DailyFocus(date=today, status="computing", created_at=datetime.now(timezone.utc))
    session.add(focus)
    session.commit()
    session.refresh(focus)

    if focus.id is None:
        raise RuntimeError("DailyFocus.id is None after insert+refresh.")

    background_tasks.add_task(_compute_daily_focus, focus.id)

    return _to_response(focus)


def _compute_daily_focus(focus_id: int) -> None:
    """Backfill -> aggregate -> generate -> extract practice positions ->
    persist. Runs after the `GET /focus/today` response has already been
    sent (via `BackgroundTasks`), so it must open its own DB session --
    the request-scoped session from `get_session` is closed by then.

    Never lets an exception propagate: any failure along the way is
    logged and the `DailyFocus` row is marked `status="error"` so the
    frontend can show a friendly message instead of polling forever.
    """
    with Session(engine) as session:
        try:
            backfill_recent_games(session)

            games = session.exec(
                select(Game)
                .where(Game.analyzed == True)  # noqa: E712
                .order_by(Game.end_time.desc())
                .limit(BACKFILL_LIMIT)
            ).all()

            focus = session.get(DailyFocus, focus_id)
            if focus is None:
                logger.error(
                    "DailyFocus id=%s vanished before background computation finished.",
                    focus_id,
                )
                return

            if len(games) < MIN_GAMES_FOR_PATTERN:
                focus.status = "insufficient_data"
                session.add(focus)
                session.commit()
                return

            aggregated = aggregate_weakness_data(games)
            focus_text = generate_daily_focus(aggregated)
            practice_positions = extract_practice_positions(games, aggregated)

            focus.status = "ready"
            focus.headline = focus_text.get("headline")
            focus.explanation = focus_text.get("explanation")
            focus.recommendation = focus_text.get("recommendation")
            focus.source_game_ids_json = json.dumps(aggregated.get("affected_game_ids", []))
            focus.practice_positions_json = json.dumps(practice_positions)
            session.add(focus)
            session.commit()
        except Exception:
            logger.exception("Failed to compute daily focus for id=%s.", focus_id)
            try:
                session.rollback()
                focus = session.get(DailyFocus, focus_id)
                if focus is not None:
                    focus.status = "error"
                    session.add(focus)
                    session.commit()
            except Exception:
                logger.exception(
                    "Failed to persist 'error' status for DailyFocus id=%s.", focus_id
                )

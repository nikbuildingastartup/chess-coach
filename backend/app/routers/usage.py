from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import require_auth
from app.db import get_session
from app.models import LlmUsage

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(require_auth)])


def _utc_date(value: datetime):
    """SQLite drops tzinfo on round-trip even for a tz-aware column, so a
    naive `created_at` here always represents a UTC instant (it was always
    written as UTC -- see `llm_usage.record_llm_usage`). Treat it as UTC
    directly rather than calling `.astimezone()`, which would misinterpret
    a naive value as local time and can shift the calendar date by a day.
    Same gotcha as `routers/games.py`'s `GameListItem._assume_utc_if_naive`.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).date()
    return value.astimezone(timezone.utc).date()


class CallSiteBreakdown(BaseModel):
    call_site: str
    calls: int
    cost_usd: float


class DayBreakdown(BaseModel):
    date: str  # UTC calendar date, "YYYY-MM-DD"
    calls: int
    cost_usd: float


class UsageSummaryResponse(BaseModel):
    total_cost_usd: float
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    since: str | None  # earliest recorded call's date ("YYYY-MM-DD"), null if no rows
    by_call_site: list[CallSiteBreakdown]
    by_day: list[DayBreakdown]  # last `days` calendar days, oldest first


@router.get("/summary", response_model=UsageSummaryResponse)
def get_usage_summary(
    days: int = 30, session: Session = Depends(get_session)
) -> UsageSummaryResponse:
    """Aggregate all recorded `LlmUsage` rows into totals, a per-call-site
    breakdown, and a per-day breakdown windowed to the last `days` calendar
    days (oldest first).

    All aggregation happens in Python rather than via SQL `GROUP BY`: the
    table is expected to stay small (one row per LLM call in a single-user
    app), and this keeps the query trivial (`SELECT *`) with the same
    tolerant-in-Python style used elsewhere in this codebase (e.g.
    `weakness_profile.py`).
    """
    rows = session.exec(select(LlmUsage)).all()

    if not rows:
        return UsageSummaryResponse(
            total_cost_usd=0,
            total_calls=0,
            total_input_tokens=0,
            total_output_tokens=0,
            since=None,
            by_call_site=[],
            by_day=[],
        )

    total_cost_usd = sum(row.total_cost_usd for row in rows)
    total_calls = len(rows)
    total_input_tokens = sum(row.prompt_tokens for row in rows)
    total_output_tokens = sum(row.completion_tokens for row in rows)

    dates = [_utc_date(row.created_at) for row in rows]
    since = min(dates).isoformat()

    call_site_calls: dict[str, int] = defaultdict(int)
    call_site_cost: dict[str, float] = defaultdict(float)
    for row in rows:
        call_site_calls[row.call_site] += 1
        call_site_cost[row.call_site] += row.total_cost_usd
    by_call_site = [
        CallSiteBreakdown(call_site=call_site, calls=call_site_calls[call_site], cost_usd=cost)
        for call_site, cost in call_site_cost.items()
    ]

    day_calls: dict[str, int] = defaultdict(int)
    day_cost: dict[str, float] = defaultdict(float)
    for row, day in zip(rows, dates):
        key = day.isoformat()
        day_calls[key] += 1
        day_cost[key] += row.total_cost_usd

    # Window is anchored to "today" (UTC) rather than the latest row's
    # date, so a `days` window behaves the way a caller expects (the last
    # N calendar days as of now) even if there happens to be no usage
    # today.
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=days - 1)

    by_day = [
        DayBreakdown(date=key, calls=day_calls[key], cost_usd=day_cost[key])
        for key in sorted(day_calls)
        if window_start.isoformat() <= key
    ]

    return UsageSummaryResponse(
        total_cost_usd=total_cost_usd,
        total_calls=total_calls,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        since=since,
        by_call_site=by_call_site,
        by_day=by_day,
    )

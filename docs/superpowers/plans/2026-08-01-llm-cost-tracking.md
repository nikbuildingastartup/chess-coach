# LLM API Cost Tracking

## Context

The app makes LLM calls to fal.ai (OpenRouter proxy → Claude Haiku 4.5) from
two places — `coaching.py` (per-game coaching text) and `focus.py` (daily
focus recommendation) — but currently discards the `response.usage` object
from every call. The user wants to track their API costs inside the tool
itself.

Claude Haiku 4.5 costs **$1.00 / million input tokens** and **$5.00 /
million output tokens** (Anthropic first-party list price; fal.ai's actual
OpenRouter markup is unknown, so this is an estimate, not a bill-exact
figure — good enough for a personal cost-awareness dashboard).

This is a small, self-contained feature: a new DB table, a shared recording
helper, a new read-only endpoint, and a compact UI badge. No existing
tables change shape, so no `_migrate_*` additions are needed in `db.py`.

## Global Constraints

- Backend: `uv`, Python >= 3.12, `backend/`. Frontend: `frontend/`, existing
  React+Vite+TS structure.
- `LlmUsage` is a **brand-new table** — `SQLModel.metadata.create_all()`
  (already called in `create_db_and_tables()`) creates it automatically.
  Do **not** add anything to `backend/app/db.py`'s `_migrate_*` functions.
- Cost is computed **at write time and stored** on the row (not recomputed
  on read at summary time) — consistent with how `Game.coaching_summary`
  and `DailyFocus`'s LLM-derived fields are already persisted once.
- Recording usage must **never raise** and must **never break** the
  coaching/focus feature it's attached to — same defensive posture
  documented in this codebase's CLAUDE.md for `coaching.py`/`focus.py`
  (missing `FAL_KEY` or any API/parse failure degrades gracefully).
- Pricing constants: `INPUT_COST_PER_TOKEN_USD = 1.00 / 1_000_000`,
  `OUTPUT_COST_PER_TOKEN_USD = 5.00 / 1_000_000`.
- All new/changed routers stay behind the existing `require_auth` shared-
  secret Bearer-token dependency, same as `games`/`play`/`focus`/`practice`.
- `uv run pytest` green / `npm run build` clean before DONE on every task.

## Task 1 — Backend: `LlmUsage` model + recording helper + wire into coaching/focus

- `backend/app/models.py`: add

  ```python
  class LlmUsage(SQLModel, table=True):
      """One row per successful fal.ai/Haiku call, for cost tracking."""

      id: int | None = Field(default=None, primary_key=True)
      created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
      call_site: str  # "coaching" | "focus"
      model: str  # e.g. "anthropic/claude-haiku-4.5" -- stored, not
                  # hardcoded, so a future model change doesn't silently
                  # mislabel old rows
      prompt_tokens: int
      completion_tokens: int
      total_tokens: int
      input_cost_usd: float
      output_cost_usd: float
      total_cost_usd: float
  ```

- New file `backend/app/llm_usage.py` (sibling to the existing shared
  `llm_json.py` helper):

  ```python
  import logging
  from datetime import datetime, timezone
  from typing import Literal

  from sqlmodel import Session

  from app.models import LlmUsage

  logger = logging.getLogger(__name__)

  INPUT_COST_PER_TOKEN_USD = 1.00 / 1_000_000
  OUTPUT_COST_PER_TOKEN_USD = 5.00 / 1_000_000


  def record_llm_usage(
      session: Session,
      call_site: Literal["coaching", "focus"],
      model: str,
      usage: object | None,
  ) -> None:
      """Persist one LlmUsage row from an OpenAI-compatible `response.usage`.

      Never raises -- recording usage is a purely optional side effect of
      a successful LLM call and must not turn a working coaching/focus
      response into a failed one.
      """
      if usage is None:
          return
      try:
          prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
          completion_tokens = getattr(usage, "completion_tokens", 0) or 0
          total_tokens = getattr(usage, "total_tokens", None) or (
              prompt_tokens + completion_tokens
          )
          input_cost = prompt_tokens * INPUT_COST_PER_TOKEN_USD
          output_cost = completion_tokens * OUTPUT_COST_PER_TOKEN_USD

          session.add(
              LlmUsage(
                  created_at=datetime.now(timezone.utc),
                  call_site=call_site,
                  model=model,
                  prompt_tokens=prompt_tokens,
                  completion_tokens=completion_tokens,
                  total_tokens=total_tokens,
                  input_cost_usd=input_cost,
                  output_cost_usd=output_cost,
                  total_cost_usd=input_cost + output_cost,
              )
          )
          session.commit()
      except Exception:
          logger.exception("Failed to record LLM usage for call_site=%s.", call_site)
          session.rollback()
  ```

- `backend/app/coaching.py`: add a `session: Session` parameter to
  `generate_coaching_summary(pgn, analysis, result, session)` (import
  `Session` from `sqlmodel`). Immediately after
  `content = response.choices[0].message.content or ""` inside the
  existing `try` block, call
  `record_llm_usage(session, "coaching", FAL_MODEL, response.usage)`.
- `backend/app/focus.py`: add a `session: Session` parameter to
  `generate_daily_focus(aggregated_data, session)` (import `Session` from
  `sqlmodel` if not already imported). Same call-site pattern:
  `record_llm_usage(session, "focus", FAL_MODEL, response.usage)` right
  after reading `content`.
- `backend/app/routers/play.py`: update the call to
  `generate_coaching_summary(...)` to pass the request-scoped `session`
  that's already in scope in `save_played_game` (it already has
  `session: Session = Depends(get_session)`).
- `backend/app/routers/focus.py`: update the call to
  `generate_daily_focus(...)` to pass the `session` already opened via
  `with Session(engine) as session:` in `_compute_daily_focus`.
- Tests (`backend/tests/`, following this codebase's existing convention of
  mocking the fal.ai/OpenAI call — never let a test hit the real fal.ai
  API):
  - New test(s) for `record_llm_usage`: given a fake `usage` object with
    known `prompt_tokens`/`completion_tokens`, asserts a row is written
    with the correct `total_tokens` and cost math (use the module's own
    pricing constants in the assertion, not hardcoded literals, so the
    test doesn't silently drift from the constants it's checking).
    Also assert it does nothing (no row written, no exception) when
    `usage` is `None`.
  - A test that `record_llm_usage` raising internally does NOT propagate
    out of `generate_coaching_summary` / `generate_daily_focus` (patch
    `record_llm_usage` to raise, assert the outer function still returns
    its normal successful result).
  - Update existing `test_coaching.py` / `test_focus.py` call sites to
    pass a `session` fixture (use this test module's existing session
    fixture pattern, e.g. an in-memory SQLite session — follow whatever
    `test_play.py`/`test_focus_router.py` already use for a test DB
    session).
  - Existing `test_play.py` / `test_focus_router.py` tests that exercise
    the full request path must still pass unchanged in behavior (only
    signature plumbing changes, not response shape).

## Task 2 — Backend: `GET /usage/summary` endpoint

Depends on Task 1 (needs the `LlmUsage` model to query).

- New file `backend/app/routers/usage.py`:
  - `APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(require_auth)])`
    — same auth pattern as the other routers.
  - Response models:

    ```python
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
    ```

  - `GET /usage/summary?days=30` (default `days=30`): fetch all `LlmUsage`
    rows via `session.exec(select(LlmUsage))`, aggregate totals and the
    `by_call_site` breakdown in Python, and bucket `created_at.date()`
    (UTC) into `by_day`, keeping only the last `days` calendar days
    (oldest first). Empty-table case returns
    `total_cost_usd=0, total_calls=0, total_input_tokens=0,
    total_output_tokens=0, since=None, by_call_site=[], by_day=[]` — not
    an error.
- `backend/app/main.py`: import and `app.include_router(usage_router)`
  alongside the existing four routers.
- Tests (`backend/tests/test_usage_router.py` or similar, following this
  codebase's existing router test conventions — see `test_focus_router.py`
  for the pattern of seeding rows via the test DB session then hitting the
  endpoint with the test client):
  - Empty table → the all-zeros/empty response described above.
  - Seed a few `LlmUsage` rows across two `call_site` values and two
    different days → assert `total_cost_usd`/`total_calls` sum correctly,
    `by_call_site` groups correctly, `by_day` buckets correctly and is
    oldest-first.
  - `days` query param actually limits the `by_day` window (seed a row
    older than the window, assert it's excluded from `by_day` but still
    counted in the running totals).
  - Endpoint requires auth (mirrors how other routers' auth is tested in
    this codebase).

## Task 3 — Frontend: usage pill in the top nav

Depends on Task 2 (needs the live endpoint to call).

- `frontend/src/api.ts`: add

  ```typescript
  export interface UsageCallSiteBreakdown {
    call_site: string;
    calls: number;
    cost_usd: number;
  }

  export interface UsageDayBreakdown {
    date: string;
    calls: number;
    cost_usd: number;
  }

  export interface UsageSummary {
    total_cost_usd: number;
    total_calls: number;
    total_input_tokens: number;
    total_output_tokens: number;
    since: string | null;
    by_call_site: UsageCallSiteBreakdown[];
    by_day: UsageDayBreakdown[];
  }
  ```

  and a `getUsageSummary(days = 30): Promise<UsageSummary>` function that
  calls `GET /usage/summary?days=${days}`, following this file's existing
  typed-fetch-wrapper pattern (same `ApiError` handling as `listGames`/
  `syncGames`).
- New `frontend/src/UsagePill.tsx`: a small always-visible component
  rendered next to the top nav (not a new tab — there is no existing
  settings screen to bury it in, and a full tab is overkill for one number
  plus a small breakdown). Shows the formatted `total_cost_usd` (e.g.
  `$0.0123`, 4 decimal places since amounts are small) as a compact pill.
  Fetches on mount via `getUsageSummary()`. Clicking the pill toggles a
  popover/dropdown showing:
  - the `by_call_site` totals (call site name, call count, cost), and
  - the last 7 entries of `by_day` (or fetch with `days=7` specifically
    for the popover's own display — either is fine, keep it simple).
  Loading state: render nothing or a neutral placeholder (e.g. `$—`) until
  the first fetch resolves — don't block the rest of the UI on it. Error
  state: swallow fetch errors quietly (log to console, hide the pill or
  show `$—`) — this is a nice-to-have display, not a critical path; it
  must never surface a blocking error to the user.
- `frontend/src/App.tsx`: render `<UsagePill />` next to the existing
  `TopNav`/nav area, visible whenever the user is past the token gate
  (same condition under which the nav itself renders). No changes to the
  `Tab` union type or tab-switching logic.
- Manual verification (no frontend test suite in this project — see
  CLAUDE.md's Tech Stack section, testing is real-Stockfish/mocked-fal.ai
  on the backend and manual browser testing for UI): `npm run build`
  clean, then `npm run dev`, log in with the token, confirm the pill
  renders with a value (or `$—` before first load), and that clicking it
  shows a sane popover in both the empty-DB state and after generating at
  least one coaching/focus call.

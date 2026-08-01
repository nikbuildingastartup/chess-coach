# Daily Focus — Analysis Progress Indicator

## Context

The Practice tab's "computing" state currently shows a static "Analyzing your
recent games..." message with no indication of how far along the background
analysis is (it can take 1-2 minutes for a full 10-game batch, per the prior
whole-branch review's noted UX gap). The user asked for a visible progress
indicator (count and/or percent) instead of a static spinner.

## Global Constraints

- Backend: `uv`, Python >= 3.12, `backend/`. Frontend: `frontend/`, existing
  React+Vite+TS structure.
- Schema change (`DailyFocus.progress_current`/`progress_total`) MUST go
  through the existing idempotent migration mechanism in `backend/app/db.py`
  (PRAGMA table_info + ALTER TABLE ADD COLUMN pattern) — nullable/defaulted,
  no NOT NULL relaxation needed.
- Progress reflects the backfill-analysis step specifically (the slow part —
  Stockfish analysis of up to `BACKFILL_LIMIT` games), not the fast
  aggregation/LLM step that follows it.
- Must not change `backfill_recent_games`'s existing return type or break
  any of its current callers/tests — add an optional progress-reporting
  hook (e.g. an optional callback parameter), don't fork the function.
- `uv run pytest` green / `npm run build` clean before DONE.

## Task 1 — Backend: track and expose analysis progress

- `backend/app/models.py`: `DailyFocus.progress_current: int = 0`,
  `DailyFocus.progress_total: int = 0`.
- `backend/app/db.py`: extend the migration for these two new nullable/
  defaulted columns, same ALTER-TABLE pattern as existing columns.
- `backend/app/analysis_backfill.py`: give `backfill_recent_games` an
  optional `on_progress: Callable[[int, int], None] | None = None`
  parameter. Call it once up front with `(0, total_candidates)` and again
  after each game's commit with `(games_done_so_far, total_candidates)`.
  Must not change existing behavior/return value when `on_progress` is
  `None` (default) — existing callers/tests keep working unchanged.
- `backend/app/routers/focus.py`'s `_compute_daily_focus`: pass an
  `on_progress` callback that updates `focus.progress_current`/
  `progress_total` on the `DailyFocus` row and commits (small, frequent
  commits are fine here — this already runs as a background task, not on
  the request path). Reset both fields to `0` when a fresh computation
  starts (including the existing insufficient_data/error retry path).
- `FocusResponse` (same file): add `progress_current: int`,
  `progress_total: int` to the response model, populated from the row.
- Tests: `backfill_recent_games` — `on_progress` called with correct
  (current, total) sequence, including zero-candidates case; existing
  tests still pass with the new optional parameter untouched. Router test:
  `GET /focus/today` while `status == "computing"` reflects incrementing
  progress across polls (mock/patch as needed to observe an intermediate
  state, following this file's existing test conventions).

## Task 2 — Frontend: display progress

Depends on Task 1.

- `frontend/src/api.ts`: add `progress_current: number`,
  `progress_total: number` to the `DailyFocus` type.
- `frontend/src/PracticePanel.tsx`: while `status === "computing"`, replace
  the static message with something reflecting real progress, e.g.
  "Analyzing your recent games... (3 of 10)" plus a simple percentage or
  progress-bar element, reusing `App.css`'s existing tokens (no new visual
  system). Handle `progress_total === 0` gracefully (e.g. "Getting
  started..." before the total is known, rather than showing "0 of 0").
- `npm run build` clean.

## Nicht Teil dieses Schritts

- No progress indicator for the LLM/aggregation step itself (fast, not
  worth instrumenting).
- No WebSocket/SSE push — polling (already established in this feature)
  stays the mechanism; progress just rides along in the existing
  `GET /focus/today` response.

## Verification

- `cd backend && uv run pytest` — green.
- `cd frontend && npm run build` — clean.
- Manual: open Practice tab on a day with unanalyzed games — progress
  indicator updates from 0 towards the total as polling continues, then
  the normal ready state appears.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

All six Phase 1 components from the design spec are implemented (Data
Ingestion, Play Module, Analysis Engine, Weakness Profile, Focus Generator,
Practice Module). The design spec remains the source of truth for original
intent and edge cases:

- [docs/superpowers/specs/2026-07-28-chess-coach-design.md](docs/superpowers/specs/2026-07-28-chess-coach-design.md)

Detailed per-feature implementation plans (with the exact decisions made,
task breakdowns, and known deferred gaps) live in
`docs/superpowers/plans/*.md`, one per feature, newest last. Read the most
recent one before extending an existing feature — it usually documents
judgment calls and edge cases not repeated here.

## What This Is

A single-user chess coaching web app. It imports the user's games from
Chess.com (or lets them play against an in-app Stockfish opponent), analyzes
every game move-by-move with Stockfish, aggregates mistakes into a weakness
profile, and surfaces one daily focus point (LLM-generated, structured
headline/explanation/recommendation) with matching practice puzzles built
from the user's own blunders.

Phase 1 is single-user — no billing or multi-tenancy — but component
boundaries (ingestion / analysis / storage / UI) are kept clean so
multi-user support can be layered on later without a rewrite. The app does
have a minimal shared-secret Bearer-token auth gate (see `backend/app/auth.py`
and `frontend`'s token-gate screen): a deliberate deviation from the design
spec's "no auth" call, since the app is intended to be publicly deployable
later.

## Build / Test Commands

Backend (`backend/`, FastAPI + SQLModel + uv):

```
cd backend && uv sync
uv run pytest
uv run fastapi dev app/main.py
```

Frontend (`frontend/`, React + Vite + TS):

```
cd frontend && npm install
npm run dev
npm run build
```

Backend tests run real Stockfish (no mocking of `chess.engine`) and real
`python-chess` PGN replay — only the fal.ai LLM calls are mocked. Never let
a test hit the real fal.ai API.

## Module Layout

Backend (`backend/app/`):

- `config.py` — settings from environment/`.env` (`APP_SECRET`,
  `CORS_ORIGINS`, `STOCKFISH_PATH`, `FAL_KEY`).
- `auth.py` — shared-secret Bearer-token dependency.
- `db.py` — SQLModel engine/session setup **and** the startup migration
  (see below — read this before any model change).
- `models.py` — `Game`, `AppSettings` (singleton, last-synced Chess.com
  username), `DailyFocus` (one row per UTC day, caches the focus
  computation + progress).
- `chesscom_client.py` — Chess.com public API client.
- `chess_engine.py` — Stockfish wrapper: `get_engine_move` (Play Module
  opponent), `analyze_game` (per-move blunder/mistake/inaccuracy/good +
  phase classification), `check_move` (Practice Module correctness check),
  `fen_before_move` (replay helper for building practice positions).
- `analysis_backfill.py` — bounded batch job: analyzes the most recent
  unanalyzed games (`BACKFILL_LIMIT`), skips Chess.com games whose
  `user_color` isn't known yet, commits per-game so partial progress
  survives a crash. Supports an optional progress callback.
- `weakness_profile.py` — pure/deterministic aggregation of flagged moves
  across recent analyzed games into a `(phase, classification)` pattern.
- `llm_json.py` — shared tolerant JSON-parsing helper for LLM responses
  (used by both `coaching.py` and `focus.py` — don't fork it again).
- `coaching.py` / `focus.py` — fal.ai (Claude Haiku via OpenRouter)
  integration for, respectively, the per-game coaching text and the daily
  focus recommendation. Both return `{headline, explanation,
  recommendation} | None` and must never raise — missing `FAL_KEY` or any
  API/parse failure degrades gracefully (deterministic fallback for
  `focus.py`, plain `None` for `coaching.py`).
- `routers/games.py`, `routers/play.py`, `routers/focus.py`,
  `routers/practice.py` — `/games`, `/play`, `/focus`, `/practice`
  endpoints, all behind `require_auth` except `GET /health`.
- `main.py` — FastAPI app wiring (CORS, routers, lifespan).

Frontend (`frontend/src/`):

- `api.ts` — typed fetch wrapper, token storage, `ApiError` with a `kind`
  field.
- `App.tsx` — token gate, top nav (Sync / Play / Practice tabs).
- `PlayPanel.tsx` / `GameTips.tsx` — play vs. engine + post-game tips.
- `PracticePanel.tsx` — daily focus card + interactive practice board,
  polls `GET /focus/today` while `status === "computing"`.

## Key Gotchas Learned Building This

- **`user_color` is required to attribute moves correctly.** A game's
  `analysis_json` entries are tagged `side: "white"|"black"`, but only
  `Game.user_color` says which side is *the user's own* moves — without
  it, weakness-profile/coaching code can't tell the user's blunders from
  the opponent's. It's set at Play-Module save time (always `"white"`)
  and backfilled at Chess.com sync time from the archive's white/black
  username fields. A game with `user_color IS NULL` must be excluded from
  aggregation, not guessed at.
- **Schema changes always go through `db.py`'s idempotent migration**
  (`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`) — never rely on
  `create_all` for a column on an *existing* table; it only creates
  brand-new tables. The real dev DB has thousands of Chess.com games and
  must never be broken by a migration.
- **Background computation (`/focus/today`) opens its own DB session**
  outside request-scoped DI (`Session(engine)` directly, not
  `Depends(get_session)`), since the request's session closes before the
  background task runs. `DailyFocus.date` is unique — concurrent
  first-request-of-the-day inserts must catch `IntegrityError`, roll back,
  and re-select the winning row rather than 500ing (React StrictMode
  double-fires effects in dev, so this race is not just theoretical).
- **fal.ai, not the OpenAI or Anthropic API directly**: `openai.OpenAI(
  base_url="https://fal.run/openrouter/router/openai/v1", api_key=
  "not-needed", default_headers={"Authorization": f"Key {FAL_KEY}"})`,
  model `"anthropic/claude-haiku-4.5"`. Chosen because the user already
  has a funded fal.ai account; the OpenAI-SDK-compatible endpoint means
  swapping models later is a one-line change.

## Development Process

This project uses the `subagent-driven-development` skill for all feature
work: a plan file per feature under `docs/superpowers/plans/`, one fresh
subagent per task (implementer, then task reviewer), a whole-branch review
before merge, fix-and-reverify loops for any findings. Each feature gets
its own git worktree/branch, PR'd and merged individually — don't stack
unrelated features on one branch.

Model selection for subagents: implementers default to Haiku (cheap), bumped
to Sonnet only for genuinely complex/judgment-heavy tasks (multi-file LLM
integration, novel architecture). Task reviewers and the final whole-branch
review always run on Sonnet or higher — review rigor is not a place to save
tokens.

## Tech Stack

- **Backend**: Python + FastAPI, `python-chess`, local Stockfish binary,
  `openai` SDK against fal.ai's OpenRouter-compatible endpoint.
- **Frontend**: React + chess.js + `react-chessboard` v5, shared between
  Play and Practice.
- **Storage**: SQLite.
- **Testing**: real Stockfish/`python-chess` for analysis logic, mocked
  fal.ai calls, manual browser testing for Play/Practice UI flows.

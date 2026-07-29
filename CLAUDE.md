# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

The Phase 1 design spec is the source of truth for architecture and scope,
but real backend and frontend code now exists on top of it:

- [docs/superpowers/specs/2026-07-28-chess-coach-design.md](docs/superpowers/specs/2026-07-28-chess-coach-design.md)

Read that file before starting implementation work; it defines the
components, data flow, edge cases, and tech stack decisions below. Update it
if scope or architecture decisions change.

## What This Is

A single-user chess coaching web app. It imports the user's games from
Chess.com (or lets them play against an in-app Stockfish opponent), analyzes
every game move-by-move with Stockfish, aggregates mistakes into a weakness
profile, and surfaces one daily focus point with matching practice puzzles
built from the user's own blunders.

Phase 1 is single-user — no billing or multi-tenancy — but component
boundaries (ingestion / analysis / storage / UI) are kept clean so
multi-user support can be layered on later without a rewrite. The app does
have a minimal shared-secret Bearer-token auth gate (see `backend/app/auth.py`
and `frontend`'s token-gate screen): this is a deliberate deviation from the
original design spec's "no auth" call, made because the app is intended to
be publicly deployable later. See the note in the design spec for details.

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

## Module Layout

Backend (`backend/app/`):

- `config.py` — settings loaded from environment/`.env`.
- `auth.py` — shared-secret Bearer-token auth dependency.
- `db.py` — SQLModel engine/session setup.
- `models.py` — `Game` table model.
- `chesscom_client.py` — Chess.com public API client (archive listing, game
  fetching).
- `routers/games.py` — `/games` endpoints (sync from Chess.com, list).
- `main.py` — FastAPI app wiring (CORS, routers, lifespan, `/health`).

Frontend (`frontend/src/`):

- `api.ts` — typed fetch wrapper, token storage, `ApiError` with a `kind`
  field for distinguishing failure modes.
- `App.tsx` — token gate, sync panel, and games list UI.

## Planned Architecture

Six components, per the design spec:

- **Data Ingestion** — pulls new games from the Chess.com public API
  (incremental sync since last fetch). Currently implemented as a full
  re-fetch of all archive months on every sync; incremental sync via
  `Last-Modified`/`If-Modified-Since` is a known follow-up, not yet done.
- **Play Module** — in-browser chessboard vs. Stockfish (adjustable
  strength); completed games feed into the same pipeline as imported games.
- **Analysis Engine** — runs every game through Stockfish, classifies each
  move (blunder/mistake/inaccuracy/good) by eval swing, tags game phase.
- **Weakness Profile** — aggregates classified mistakes across games into
  recurring patterns.
- **Focus Generator** — picks the single highest-impact focus point per day;
  computed once daily and cached.
- **Practice Module** — presents puzzles sourced from the user's own blunder
  positions with solved/not-solved feedback.

Daily data flow: sync new games → analyze unanalyzed games (imported or
played in-app) → update weakness profile → recompute cached focus point →
show it with matching practice positions.

## Tech Stack

- **Backend**: Python + FastAPI, `python-chess` for game logic, a local
  Stockfish binary for both analysis and as the Play Module opponent.
- **Frontend**: React with an interactive chessboard component (e.g.
  `react-chessboard`) shared between Play and Practice.
- **Storage**: SQLite.
- **Testing**: unit tests for analysis logic (blunder classification,
  weakness aggregation) against fixed reference games; Play/Practice flows
  are tested manually in the browser.

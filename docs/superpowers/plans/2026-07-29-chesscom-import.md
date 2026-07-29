# Chess.com Import — Implementation Plan

## Context

Repo currently has only docs (design spec, CLAUDE.md, mockup handoff) — no
code. This plan implements the first vertical slice per
[docs/superpowers/specs/2026-07-28-chess-coach-design.md](../specs/2026-07-28-chess-coach-design.md):
a FastAPI backend that imports the user's games from Chess.com into SQLite,
and a React frontend to trigger sync and view imported games. Analysis
Engine, Weakness Profile, Focus Generator, Play/Practice Module, and
Stockfish integration are explicitly out of scope — separate future plans.

Decisions already made with the user (do not re-litigate):
- Backend tooling: `uv` (Python), FastAPI, SQLModel, httpx.
- Frontend tooling: Vite + React + TypeScript.
- The app is meant to be publicly deployable later, so even this Phase 1
  slice gets a minimal shared-secret Bearer-token auth gate on all
  data endpoints (not full user auth — one shared secret, checked via a
  FastAPI dependency).
- Storage: SQLite via SQLModel, one `Game` table (raw game data). Move-level
  analysis schema is a future plan's concern.
- Frontend/backend split so the backend could later move to a host with
  persistent storage while the frontend deploys to Vercel — no code for
  that migration now, just don't hardcode `localhost` assumptions.

## Global Constraints

- Backend lives in `backend/`, managed by `uv` (`uv sync`, `uv run pytest`,
  `uv run fastapi dev app/main.py`). Python >= 3.12.
- Frontend lives in `frontend/`, scaffolded via
  `npm create vite@latest . -- --template react-ts`.
- All backend config (host, port, CORS origins, the shared secret) comes
  from environment variables via a `pydantic-settings` `Settings` class in
  `backend/app/config.py` — no hardcoded `localhost` or secrets in code.
  Provide a `backend/.env.example` documenting required vars
  (`APP_SECRET`, `CORS_ORIGINS`).
- Auth: single shared secret from `Settings.app_secret`. A FastAPI
  dependency (`backend/app/auth.py`) checks `Authorization: Bearer <secret>`
  on every route except `GET /health`. Missing/wrong token → 401.
- Chess.com API calls MUST set a descriptive `User-Agent` header (Chess.com
  policy requires this) — e.g. `"chess-coach (contact: <not required to be
  real, just non-empty>)"`.
- `Game.chesscom_game_id` is unique (DB-level constraint) — sync must be
  idempotent: re-running sync for the same user must not create duplicate
  rows.
- Every task's tests must pass via `uv run pytest` (backend) before the
  task is reported DONE. No task is DONE with failing or skipped tests.
- Do not implement Stockfish, move analysis, weakness profiles, or the
  Play/Practice UI in this plan — flag as out of scope if tempted.

## Task 1 — Backend skeleton: config, auth, db, health check

Set up the `uv`-managed backend project from scratch:

- `backend/pyproject.toml`: FastAPI (`fastapi[standard]`), `sqlmodel`,
  `httpx`, `pydantic-settings` as runtime deps; `pytest`, `pytest-asyncio`,
  `respx` (for mocking httpx in later tasks) as dev deps.
- `backend/app/config.py`: `Settings(BaseSettings)` with `app_secret: str`,
  `cors_origins: list[str]`, `database_url: str = "sqlite:///./chess_coach.db"`,
  loaded from env / `.env`.
- `backend/app/auth.py`: `require_auth` FastAPI dependency comparing the
  `Authorization: Bearer <token>` header against `Settings.app_secret`.
  Raise `HTTPException(401)` on mismatch or missing header.
- `backend/app/db.py`: SQLModel `engine` from `Settings.database_url`, a
  `create_db_and_tables()` function, and a `get_session` dependency.
- `backend/app/main.py`: FastAPI app, CORS middleware using
  `Settings.cors_origins`, calls `create_db_and_tables()` on startup, and a
  `GET /health` route that returns `{"status": "ok"}` with **no** auth
  dependency (everything else added in later tasks requires auth).
- `backend/.env.example` documenting `APP_SECRET` and `CORS_ORIGINS`.
- Tests: a test for `require_auth` (valid token passes, missing/invalid
  token raises 401) and a test that `GET /health` returns 200 with no
  `Authorization` header.

## Task 2 — Game model + Chess.com client

Depends on Task 1 (`db.py`, `config.py` must exist).

- `backend/app/models.py`: SQLModel `Game` table —
  `id: int | None` (PK, autoincrement), `chesscom_game_id: str` (unique,
  indexed), `pgn: str`, `end_time: datetime`, `time_class: str`,
  `result: str`, `analyzed: bool = False`.
- `backend/app/chesscom_client.py`: async client using `httpx.AsyncClient`
  with the required `User-Agent` header. Two functions:
  - `get_archive_urls(username: str) -> list[str]` — calls
    `GET https://api.chess.com/pub/player/{username}/games/archives`,
    returns the `archives` list from the JSON response.
  - `get_games_for_month(archive_url: str) -> list[dict]` — calls the given
    archive URL, returns the `games` list from the JSON response (each dict
    has Chess.com's raw game fields: `url`, `pgn`, `end_time`, `time_class`,
    etc. — `url` or a derived id is the source for `chesscom_game_id`).
  - Handle unreachable/non-2xx responses by raising a
    `ChessComUnavailableError` (custom exception in the same module) rather
    than letting `httpx` exceptions propagate raw — this is what Task 3's
    endpoint will catch to implement the "API unreachable → degrade
    gracefully" edge case from the design spec.
- Tests: use `respx` to mock both endpoints. Cover: successful archive +
  games fetch; a non-2xx response raising `ChessComUnavailableError`; a
  network-error (e.g. `httpx.ConnectError`) also raising
  `ChessComUnavailableError`.

## Task 3 — Sync endpoint + games list endpoint

Depends on Task 2 (`models.py`, `chesscom_client.py`).

- `backend/app/routers/games.py`:
  - `POST /games/sync` — body `{"username": str}` (Pydantic model). Behind
    `require_auth`. Calls the Chess.com client for all archive months,
    upserts games into SQLite keyed on `chesscom_game_id` (skip if already
    present — idempotent), returns
    `{"imported": <n new games>, "total": <n games for user in DB>}`. If
    `ChessComUnavailableError` is raised, return HTTP 502 with a JSON error
    body (`{"detail": "..."}"`) rather than a 500 — the frontend needs a
    distinguishable "Chess.com unreachable" case.
  - `GET /games` — behind `require_auth`. Returns all stored games ordered
    by `end_time` descending, as a list of
    `{chesscom_game_id, end_time, time_class, result}` (no need to return
    the full PGN for the list view).
- Wire the router into `backend/app/main.py`.
- Tests: `POST /games/sync` with a mocked Chess.com client — first call
  imports N games, second call with the same data imports 0 new (dedup
  works). `POST /games/sync` returns 401 without a valid token.
  `POST /games/sync` returns 502 when the Chess.com client raises
  `ChessComUnavailableError`. `GET /games` returns stored games in
  descending `end_time` order.

## Task 4 — Frontend scaffold + sync UI

Depends on Task 3 (needs the real API shape to build against — endpoints
and JSON fields are frozen by then).

- Scaffold `frontend/` via
  `npm create vite@latest . -- --template react-ts` (run from inside
  `frontend/`, i.e. the repo ends up with `frontend/package.json` etc., not
  a nested extra folder).
- `frontend/src/api.ts`: a small fetch wrapper. Reads the Bearer token from
  `localStorage` (key `chess-coach-token`) and the API base URL from a Vite
  env var (`import.meta.env.VITE_API_BASE_URL`, fallback
  `http://localhost:8000` for local dev — no other hardcoded host).
  Exposes `syncGames(username: string)` (POST `/games/sync`) and
  `listGames()` (GET `/games`), both throwing a typed error on non-2xx so
  the UI can distinguish "unauthorized" (401 → prompt for token again) from
  "Chess.com unreachable" (502 → show a specific message) from other
  errors.
- `frontend/src/App.tsx`:
  - If no token in `localStorage`, show a single password/token input
    (stores it in `localStorage` on submit — no validation call needed,
    the next API call will 401 if it's wrong and should re-prompt).
  - Once a token is set: a Chess.com username input + "Sync" button
    (calls `syncGames`, shows a loading state, then either the
    imported/total counts or an error message — specifically distinguish
    the "Chess.com unreachable" case per the design spec's edge case).
  - A games list (calls `listGames` on mount and after a successful sync)
    showing date, time class, result for each stored game.
- No automated frontend tests required for this task (design spec says
  Play/Practice flows are tested manually in the browser; this UI is
  simple enough to fall under the same manual-testing note) — but the
  implementer must run `npm run build` to confirm it compiles clean, and
  report that in the task report.

## Verification (end-to-end, after all tasks)

- `cd backend && uv run pytest` — all tests pass.
- `cd backend && uv run fastapi dev app/main.py` — `GET /health` returns
  200 without auth; `GET /games` returns 401 without a token.
- Manual: with a real Chess.com username, `POST /games/sync` (with the
  configured `APP_SECRET` as Bearer token) imports real games into
  `chess_coach.db`; a second call imports 0 new games.
- `cd frontend && npm run build` succeeds; `npm run dev` — token prompt →
  username + sync → games list renders with real imported data.

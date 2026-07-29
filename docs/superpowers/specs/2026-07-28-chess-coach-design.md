# Chess Coach — Phase 1 Design

## Problem

The user currently plays a lot of chess but improves by feel — no deliberate
practice (no puzzles, no opening study, no structured review). They need a
personal coach that tells them, each day, the single most impactful thing to
work on, grounded in analysis of their actual games.

## Scope

Phase 1 is a **single-user** tool for the user's own improvement. It is
architected with clear boundaries (data ingestion, analysis, storage, UI) so
it *can* be extended to a multi-user product later, but multi-user concerns
(auth, billing, multi-tenancy) are explicitly out of scope for this phase and
will get their own design cycle.

## Core Concept

The user opens a web app and sees one thing front and center: today's focus
point (e.g. "Practice: spotting hanging pieces in the middlegame"), derived
from analysis of their real games. They can immediately practice it via an
interactive board loaded with positions from their own past mistakes.

In addition to importing games from Chess.com, the user can play games
directly against the app (Stockfish, adjustable strength) at any time. These
in-app games are analyzed exactly like imported games and feed into the same
weakness profile — this is a permanent feature, not a one-time placement
test.

## Components

- **Data Ingestion** — Pulls games from the user's Chess.com account via the
  public Chess.com API, given a username. Detects and fetches only new games
  since the last sync.
- **Play Module** — Interactive chessboard in the browser where the user
  plays against Stockfish at an adjustable strength. Completed games are
  stored the same way as imported games.
- **Analysis Engine** — Runs every game (imported or played in-app) through
  Stockfish. Classifies each move (blunder / mistake / inaccuracy / good) via
  eval swing, and tags it with game phase (opening / middlegame / endgame).
- **Weakness Profile** — Aggregates classified mistakes across all of the
  user's games into recurring patterns (e.g. "frequently loses material to
  missed forks in the middlegame").
- **Focus Generator** — Selects the single highest-impact focus point for
  the current day from the weakness profile. Recomputed once per day and
  cached until the next day.
- **Practice Module** — Interactive board presenting puzzles built from the
  user's own blunder positions, with immediate solved/not-solved feedback.
- **Storage** — Local database holding games, per-move analysis, weakness
  profile state, and practice history.

## Data Flow (daily)

1. User opens the app.
2. App checks Chess.com for new games since the last sync and imports them.
3. Any unanalyzed games (imported or played in-app) are run through the
   Analysis Engine.
4. Weakness Profile is updated from the newly analyzed games.
5. Focus Generator computes today's focus point (once per day, cached).
6. User sees "Today: [focus point]" plus matching practice positions to
   solve immediately.

## Edge Cases

- **Cold start (no games yet)**: App prompts the user to either enter their
  Chess.com username or play a game against the app directly to start
  generating data.
- **Chess.com API unreachable**: Error is shown; app continues to operate
  on the last known data rather than failing.
- **Insufficient data for patterns** (e.g. only 2 games analyzed): Focus
  Generator falls back to a simple generic first recommendation instead of
  speculating on thin patterns.

## Tech Stack

- **Backend**: Python + FastAPI. `python-chess` for game logic. A local
  Stockfish binary is used both for move analysis and as the opponent in the
  Play Module.
- **Frontend**: React web UI with an interactive chessboard component (e.g.
  `react-chessboard`) shared by the Play and Practice modules.
- **Storage**: SQLite — sufficient for single-user, straightforward to
  migrate later if the app grows to multi-user.
- **Testing**: Unit tests for the analysis logic (blunder classification,
  weakness aggregation) against fixed reference games. Manual testing of the
  Play and Practice flows in the browser.

## Out of Scope (Phase 1)

- Multi-user accounts, billing, multi-tenancy. (Deviation: a minimal
  shared-secret Bearer-token auth gate was added during implementation,
  since the app is intended to be publicly deployable later — full
  multi-user auth is still out of scope.)
- Push/email notifications for the daily focus point (user opens the app
  themselves).
- Dashboard/report views beyond the daily focus point (e.g. long-term trend
  charts) — may be added later but is not required for the initial version.

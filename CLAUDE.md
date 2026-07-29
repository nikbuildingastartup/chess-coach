# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

This repository currently contains only the Phase 1 design spec — no
application code has been written yet. The spec is the source of truth for
architecture and scope until implementation begins:

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

Phase 1 is explicitly single-user — no auth, billing, or multi-tenancy — but
component boundaries (ingestion / analysis / storage / UI) are kept clean so
multi-user support can be layered on later without a rewrite.

## Planned Architecture

Six components, per the design spec:

- **Data Ingestion** — pulls new games from the Chess.com public API
  (incremental sync since last fetch).
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

## Tech Stack (planned)

- **Backend**: Python + FastAPI, `python-chess` for game logic, a local
  Stockfish binary for both analysis and as the Play Module opponent.
- **Frontend**: React with an interactive chessboard component (e.g.
  `react-chessboard`) shared between Play and Practice.
- **Storage**: SQLite.
- **Testing**: unit tests for analysis logic (blunder classification,
  weakness aggregation) against fixed reference games; Play/Practice flows
  are tested manually in the browser.

Once code exists, this file should be updated with actual build/lint/test
commands and the real module layout.

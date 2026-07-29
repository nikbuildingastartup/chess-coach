# Chess Coach — Mockup Handoff

Purpose: brief for a Claude Design mockup run. Gives product context, screens
needed, and constraints so mockups can be generated without re-deriving them
from the codebase. Source of truth for scope/architecture is
[docs/superpowers/specs/2026-07-28-chess-coach-design.md](../superpowers/specs/2026-07-28-chess-coach-design.md).

## Product in one sentence

A single-user chess coaching web app: it tells the user, each day, the one
thing to practice next — grounded in analysis of their own real games — and
lets them practice it immediately on an interactive board.

## Who it's for

One user (the app owner), improving at chess by feel today (no deliberate
practice). Not a multi-user product in this phase — no login/signup screens,
no account switching, no team/sharing UI.

## Core user flow (the thing to nail visually)

1. User opens the app.
2. Sees **one thing front and center**: today's focus point, e.g. "Practice:
   spotting hanging pieces in the middlegame." This is the hero of the whole
   app — it should not compete with navigation chrome or secondary content.
3. Below/next to it: an interactive chess board pre-loaded with a puzzle
   built from the user's own past mistakes, matching that focus point.
4. User solves it, gets immediate solved/not-solved feedback, can move to
   the next matching puzzle.

Everything else (game list, sync status, play-vs-Stockfish) is secondary and
should visually recede behind this daily-focus moment.

## Screens to mock up

1. **Daily Focus (home)** — the screen above. Primary content: focus point
   headline + short "why" (e.g. which pattern it's based on, how many times
   it's recurred), practice board, solved/not-solved state, "next puzzle"
   action.
2. **Cold start / empty state** — no games analyzed yet. Prompts the user to
   either enter a Chess.com username to import games, or start a game
   against the app directly. No focus point exists yet; don't fake one.
3. **Sync / Import** — Chess.com username entry, sync status (checking for
   new games / importing / done), list of imported games (date, result,
   time control). Needs a state for "Chess.com API unreachable" that
   degrades gracefully (shows last known data, not a hard error page).
4. **Play** — interactive board vs. Stockfish, with an adjustable-strength
   control. This is a permanent feature, not a placement test — should feel
   like a normal "play a game" screen, not a wizard/onboarding flow.
5. **Practice** — standalone puzzle-solving view (same board component as
   Daily Focus, but browsing/filtering puzzles built from the user's own
   blunders rather than a single daily pick).
6. **Auth gate** — minimal: a single password/token entry screen before
   anything else loads, since the app may be deployed publicly. No
   registration, no "forgot password," no multi-account UI — one shared
   secret, one user.

## Explicitly out of scope for mockups

- Multi-user/account UI (login-as-different-user, teams, sharing).
- Notifications (push/email) — the user opens the app themselves.
- Trend/analytics dashboards beyond the daily focus point (long-term charts,
  rating graphs, etc.) — may come later, not this phase.

## Shared component

The chess board is a **shared interactive component** used identically in
Play and Practice (and embedded in Daily Focus) — likely `react-chessboard`.
Mock it as one reusable element with consistent piece style/board theme
across all screens, not a bespoke board per screen.

## Tone / visual direction

No existing brand system to match against — this is a personal tool, not a
commercial product. Favor a calm, focused, low-chrome layout that puts the
daily focus point and the board front and center (see "Core user flow"
above); avoid dashboard-style density or gamified badge/streak UI unless the
user asks for it later — not in the current spec.

## Technical constraints mockups should respect

- Frontend: React + TypeScript, built with Vite.
- Board interactions must work with `react-chessboard`-style props (FEN/PGN
  in, move callbacks out) — avoid designing board interactions that this
  component can't express (e.g. exotic drag gestures).
- Single-column-friendly: this is used by one person, likely on a laptop,
  but shouldn't assume a huge viewport.

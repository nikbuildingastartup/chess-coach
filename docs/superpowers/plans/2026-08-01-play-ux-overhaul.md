# Play vs. Engine — UX Overhaul

## Context

The Play tab currently works but feels minimal (see screenshot): a small,
cramped board with no move history, no captured-piece tracking, no visual
feedback for the last move or check, plain-text strength labels ("Easy" /
"Medium" / "Hard" with no sense of actual difficulty), and no indication of
where a selected piece can legally move. The user wants to "10x" this
specific tab's UX.

Scope decided with the user across two rounds of questions:
1. Direction: interaction & visual polish (not live in-game coaching
   feedback — that stays explicitly out of scope, as decided earlier this
   session).
2. Concrete elements selected: live move list, captured pieces, last-move +
   check highlighting.
3. Explicitly declined for this round: sound effects, clickable
   post-game tips list with board preview, resign-confirmation/undo.
4. Follow-up requirements added directly by the user: Elo-labeled strength
   buttons, legal-move indicator dots, a material-advantage indicator, and a
   visibly bigger board with more room to breathe.

This is a **frontend-only** change — `PlayPanel.tsx` and `App.css`. No
backend/API changes, no new endpoints, no schema changes. Scope is
`PlayPanel.tsx` specifically (not the Practice board, not GameTips' post-game
list beyond what already exists there).

## Verified technical facts (checked against the installed package)

`react-chessboard@5.10.0`'s `ChessboardOptions` type
(`frontend/node_modules/react-chessboard/dist/ChessboardProvider.d.ts`)
confirms the exact props to use — do not guess alternate names:
- `squareStyles?: Record<string, React.CSSProperties>` — for last-move,
  check, and legal-move-dot highlighting (keyed by square, e.g. `"e4"`).
- `onSquareClick?: ({ piece, square }) => void` and
  `onPieceClick?: ({ isSparePiece, piece, square }) => void` — for
  click-to-select.
- `onPieceDrag?: ({ isSparePiece, piece, square }) => void` — fires when a
  drag starts, usable to compute/show legal-move dots during drag too.
- Existing `onPieceDrop`/`allowDragging`/`position`/`id` usage in
  `PlayPanel.tsx` is unchanged.

`chess.js`'s `chess.moves({ square, verbose: true })` returns the legal
moves from a square (each with `.to`, and `.captured` when it's a capture).
`chess.history({ verbose: true })` returns played moves with `.captured`
(piece type taken, if any) and `.color` (mover) — the basis for both the
move list and captured-pieces tracking. `chess.board()` gives the full
8x8 array of `{type, color} | null` for material-count and king-square
(check highlight) lookups.

## Global Constraints

- Frontend only (`frontend/`). No backend changes.
- Reuse `App.css`'s existing "Zinc" theme tokens (`--serif`, `--text-h`,
  `--text-muted`, `--border`, `--border-strong`, `.card`) for all new UI —
  no new visual system, no new color palette.
- Elo labels are explicitly approximate (Stockfish's Skill Level-to-Elo
  mapping is not exact or officially guaranteed) — label them with a `~`
  prefix (e.g. "Easy · ~1300 Elo"), don't imply false precision.
- No new npm dependencies — everything needed (move list, captured pieces,
  material count, legal-move highlighting) is derivable from chess.js's
  existing API already in use.
- Resign/New game button behavior and position stay exactly as-is (no
  confirmation dialog, no undo — explicitly out of scope this round).
- `npm run build` clean before each task is DONE.

## Task 1 — Layout shell, move list, captured pieces, material indicator, Elo labels

- `frontend/src/App.css` / `PlayPanel.tsx`: restructure the Play tab into a
  two-column layout once a game is in progress — board on the left (larger
  than today; loosen whatever fixed width/max-width currently constrains
  `.board-container`), a sidebar on the right containing (top to bottom):
  strength selector OR keep it above the board (implementer's call on the
  cleanest placement now that the layout is two-column), material-advantage
  badge, captured-pieces row, move list. Add a single responsive breakpoint
  (e.g. below ~900px) that stacks the sidebar below the board instead of
  beside it — don't over-engineer further breakpoints.
- `STRENGTH_OPTIONS` labels: change to include an approximate Elo, e.g.
  `"Easy · ~1300 Elo"`, `"Medium · ~1900 Elo"`, `"Hard · ~2500 Elo"`
  (Stockfish Skill Levels 3/10/18 respectively, per
  `backend/app/chess_engine.py`'s `SKILL_LEVELS` — these are commonly-cited
  community approximations, not exact; implementer should sanity-check
  the numbers but doesn't need backend changes, this is a frontend label
  only).
- Move list: derive from `chessRef.current.history()` at render time (no
  new state needed — it already re-renders on every `fen` change). Render
  as a simple two-column scoresheet (move number | White move | Black
  move), scrollable if it grows long, auto-scrolled/pinned to the latest
  move.
- Captured pieces: derive from `chessRef.current.history({ verbose: true
  })`'s `.captured`/`.color` fields, accumulate into two lists (pieces
  White has captured from Black, and vice versa), render as small piece
  glyphs (reuse whatever piece rendering convention is simplest — Unicode
  chess symbols are a reasonable, dependency-free choice) grouped by side.
- Material advantage: compute total material (standard values P1/N3/B3/R5/
  Q9) for both sides from `chessRef.current.board()`, show the signed
  difference as a small badge (e.g. "White +3", "Even") near the captured
  pieces.
- `npm run build` clean.

## Task 2 — Board interactivity: legal-move indicators, last-move & check highlighting

Depends on Task 1 only for the layout shell existing (not a hard blocker —
could be built against the current layout and slot in, implementer's
judgment on the cleanest order, but do Task 1 first as planned).

- Last-move highlighting: track the most recent move's `from`/`to` squares
  (capture them explicitly at the point each move — human or engine — is
  applied via `chess.move(...)`'s returned move object, which has `.from`/
  `.to`; don't try to derive this from a FEN diff). Feed into the
  `squareStyles` prop with a subtle background-color style reusing an
  existing muted theme token.
- Check highlighting: when `chessRef.current.inCheck()` is true, find the
  king square of the side to move (scan `chessRef.current.board()` for
  `{type: "k", color: chessRef.current.turn()}`) and add a distinct
  (red-ish, but reuse an existing token if one fits, e.g. whatever token
  the Sync screen's "Lost" outcome uses) highlight to `squareStyles` for
  that square.
- Legal-move indicators: on `onPieceDrag` (drag start) AND on
  `onSquareClick`/`onPieceClick` (click-to-select — add click-to-select-
  then-click-to-move support alongside the existing drag-and-drop, since
  showing legal-move dots is only useful if there's a way to trigger it
  without already mid-drag), compute `chessRef.current.moves({ square,
  verbose: true })` for the selected square and add a dot/marker style
  (via `squareStyles`) to each legal destination square — differentiate
  capture-destination styling from quiet-move-destination styling if it's
  easy to do cleanly, otherwise one consistent dot style for all legal
  targets is fine. Clicking a highlighted destination square should
  execute that move (reusing the same move-application/engine-request
  logic already used by `onPieceDrop` — refactor the shared "apply this
  move, check game over, request engine reply" logic into one function
  both the drop handler and the click-to-move handler call, rather than
  duplicating it). Clear the selection/highlight on a completed move, on
  selecting a different piece, or on clicking an empty non-legal square.
- All three highlight sources (last-move, check, legal-move dots) need to
  merge cleanly into one `squareStyles` object passed to the board — don't
  let them clobber each other if a square happens to need more than one
  style at once (e.g. a legal-move destination that's also where check
  would land).
- `npm run build` clean.

## Nicht Teil dieses Schritts

- Sound effects.
- Clickable post-game tips list / board preview per move (GameTips.tsx
  stays as-is).
- Resign confirmation dialog or undo/takeback.
- Live in-game coaching feedback (still explicitly deferred, as decided
  earlier this session).
- Any change to the Practice tab's board (`PracticePanel.tsx`) — this plan
  is scoped to the Play-vs-engine tab only.
- Further responsive breakpoints beyond the one stack/unstack point.

## Verification

- `cd frontend && npm run build` — clean after each task.
- Manual (after both tasks): play a full game — board is visibly larger,
  strength buttons show Elo, dragging or clicking a piece shows legal-move
  dots, the last move (yours and the engine's) is highlighted, being put in
  check highlights your king, the move list fills in correctly for both
  sides, captured pieces and the material badge update after every capture.
- Manual: resize the browser narrower than ~900px — sidebar stacks below
  the board instead of squeezing it, board stays usable.

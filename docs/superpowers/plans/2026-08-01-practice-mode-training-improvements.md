# Practice Mode Training Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four explicitly-deferred gaps in the Practice Module: no
persistent solve history, only one weakness pattern surfaced, a fixed
once-a-day puzzle set, and silent failures when a puzzle can't be built.

**Architecture:** A new `PracticeAttempt` table gives each practice puzzle
(identified by `(game_id, move_number, side)`) a stable, persistent solve
state. `weakness_profile.aggregate_weakness_data` is generalized from a
single top pattern to a ranked top-3. `focus.extract_practice_positions`
interleaves puzzles across those patterns, skips already-solved positions,
re-queues previously-wrong ones first, and reports how many positions it
had to skip. A new `GET /practice/positions` endpoint generates a fresh
puzzle set on demand (independent of the once-a-day cached focus text),
and `POST /practice/check-move` now records solve state when the caller
identifies which puzzle was attempted. The frontend fetches puzzle sets
from the new endpoint instead of cycling the same 5 positions forever, and
surfaces solved/total progress plus a skipped-puzzle warning.

**Tech Stack:** FastAPI + SQLModel + python-chess + local Stockfish
(backend); React + TypeScript + chess.js + react-chessboard (frontend).

## Global Constraints

- Backend tests run real Stockfish and real python-chess PGN replay — never
  mock `chess.engine`. Only fal.ai LLM calls are mocked.
- Schema changes to *existing* tables must go through `db.py`'s idempotent
  migration (`PRAGMA table_info` + `ALTER TABLE`). A brand-new table does
  not need a migration function — `SQLModel.metadata.create_all()` already
  issues `CREATE TABLE IF NOT EXISTS` for it.
- `Game.user_color` gates weakness aggregation; games with `user_color IS
  NULL` are already excluded by `aggregate_weakness_data` and that must
  keep working unchanged.
- Re-queue of incorrectly solved puzzles is a simple "surface it again in
  a later set" — no time-based spaced-repetition scheduling.

---

## Task 1: `PracticeAttempt` model

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `PracticeAttempt` SQLModel table with fields `id`, `game_id`,
  `move_number`, `side`, `fen`, `solved: bool`, `attempts_count: int`,
  `last_attempted_at: datetime | None`, `created_at: datetime`, and a
  unique constraint on `(game_id, move_number, side)`. Later tasks query
  it via `select(PracticeAttempt).where(PracticeAttempt.game_id == ...,
  PracticeAttempt.move_number == ..., PracticeAttempt.side == ...)`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_models.py` (reuses the existing `session`
fixture at the top of that file):

```python
from sqlalchemy.exc import IntegrityError
from app.models import PracticeAttempt


def test_practice_attempt_can_be_created_with_defaults(session):
    attempt = PracticeAttempt(
        game_id=1,
        move_number=3,
        side="white",
        fen="8/8/8/8/8/8/8/8 w - - 0 1",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    assert attempt.id is not None
    assert attempt.solved is False
    assert attempt.attempts_count == 0
    assert attempt.last_attempted_at is None


def test_practice_attempt_position_is_unique(session):
    kwargs = dict(
        game_id=1,
        move_number=3,
        side="white",
        fen="8/8/8/8/8/8/8/8 w - - 0 1",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    session.add(PracticeAttempt(**kwargs))
    session.commit()

    session.add(PracticeAttempt(**kwargs))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_models.py -k practice_attempt -v`
Expected: FAIL with `ImportError: cannot import name 'PracticeAttempt'`

- [ ] **Step 3: Add the model**

In `backend/app/models.py`, add the `UniqueConstraint` import and the new
table at the end of the file:

```python
from sqlalchemy import Column, DateTime, UniqueConstraint
```

```python
class PracticeAttempt(SQLModel, table=True):
    """Persisted solve state for one practice puzzle, identified by the
    game move it was drawn from (`game_id`, `move_number`, `side` --
    matches `fen_before_move`'s parameters and `analyze_game`'s per-move
    tagging). Lets puzzles survive across sessions/days: solved puzzles
    are skipped in future sets, incorrectly-solved ones are re-queued."""

    __table_args__ = (
        UniqueConstraint("game_id", "move_number", "side", name="uq_practiceattempt_position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    game_id: int = Field(foreign_key="game.id", index=True)
    move_number: int
    side: str
    fen: str
    solved: bool = False
    attempts_count: int = 0
    last_attempted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: PASS (all tests in the file, including the two new ones)

- [ ] **Step 5: Verify the new table is created on a real startup path**

Run: `cd backend && uv run pytest tests/test_db_migration.py -v`
Expected: PASS unchanged — confirms `create_db_and_tables()` (which the
new table relies on via plain `create_all`) still works against the
existing migration test fixtures.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/tests/test_models.py
git commit -m "feat: add PracticeAttempt model for persistent puzzle solve state"
```

---

## Task 2: Top-3 weakness patterns in `weakness_profile.py`

**Files:**
- Modify: `backend/app/weakness_profile.py`
- Test: `backend/tests/test_weakness_profile.py`

**Interfaces:**
- Consumes: nothing new (pure function on `Game` rows, unchanged from
  today).
- Produces: `aggregate_weakness_data(games)` return dict gains two new
  keys, additive and backward-compatible with the existing `top_pattern`
  / `top_pattern_moves` keys (unchanged):
  - `"top_patterns"`: `list[{"phase": str, "classification": str, "count":
    int}]`, ranked same as `top_pattern`, up to `MAX_TOP_PATTERNS` (3)
    entries.
  - `"moves_by_pattern"`: `dict[str, list[dict]]` keyed by `"<phase>:
    <classification>"` (same key format as `counts_by_pattern`), each
    value the matching flagged moves sorted newest-game-first, restricted
    to the patterns in `top_patterns`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_weakness_profile.py`:

```python
def test_aggregate_returns_up_to_three_ranked_top_patterns():
    game = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[
            _entry(2, "a", "white", "blunder", "opening"),
            _entry(4, "b", "white", "blunder", "opening"),
            _entry(6, "c", "white", "blunder", "opening"),
            _entry(10, "d", "white", "mistake", "middlegame"),
            _entry(12, "e", "white", "mistake", "middlegame"),
            _entry(40, "f", "white", "blunder", "endgame"),
        ],
    )

    result = aggregate_weakness_data([game])

    assert result["top_patterns"] == [
        {"phase": "opening", "classification": "blunder", "count": 3},
        {"phase": "middlegame", "classification": "mistake", "count": 2},
        {"phase": "endgame", "classification": "blunder", "count": 1},
    ]


def test_aggregate_caps_top_patterns_at_three():
    game = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[
            _entry(2, "a", "white", "blunder", "opening"),
            _entry(10, "b", "white", "mistake", "middlegame"),
            _entry(40, "c", "white", "blunder", "endgame"),
            _entry(41, "d", "white", "mistake", "endgame"),
        ],
    )

    result = aggregate_weakness_data([game])

    assert len(result["top_patterns"]) == 3


def test_aggregate_moves_by_pattern_covers_each_top_pattern_newest_first():
    game1 = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[_entry(10, "older-mistake", "white", "mistake", "middlegame")],
    )
    game2 = _game(
        game_id=2,
        end_time=BASE_TIME + timedelta(days=1),
        analysis=[
            _entry(2, "opening-blunder", "white", "blunder", "opening"),
            _entry(10, "newer-mistake", "white", "mistake", "middlegame"),
        ],
    )

    result = aggregate_weakness_data([game1, game2])

    assert set(result["moves_by_pattern"].keys()) == {"opening:blunder", "middlegame:mistake"}
    assert [m["san"] for m in result["moves_by_pattern"]["middlegame:mistake"]] == [
        "newer-mistake",
        "older-mistake",
    ]


def test_aggregate_top_patterns_empty_when_no_flagged_moves():
    game = _game(
        game_id=1,
        end_time=BASE_TIME,
        analysis=[_entry(1, "e4", "white", "good", "opening")],
    )

    result = aggregate_weakness_data([game])

    assert result["top_patterns"] == []
    assert result["moves_by_pattern"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_weakness_profile.py -k top_patterns -v`
Expected: FAIL with `KeyError: 'top_patterns'`

- [ ] **Step 3: Implement `_pick_top_patterns` and wire it into `aggregate_weakness_data`**

In `backend/app/weakness_profile.py`, add the constant near
`MIN_GAMES_FOR_PATTERN`:

```python
# How many distinct (phase, classification) patterns to surface for
# practice puzzle generation -- more than a handful stops being "your top
# weaknesses" and starts being "every mistake you've ever made".
MAX_TOP_PATTERNS = 3
```

Replace `_pick_top_pattern` (the whole function) with:

```python
def _pick_top_patterns(
    counts: Counter[tuple[str, str]], all_flagged: list[dict[str, Any]], n: int = 1
) -> list[tuple[str, str, int]]:
    """Rank (phase, classification) patterns by frequency, breaking ties
    deterministically (blunder over mistake, then most-recent occurrence)
    since `Counter.most_common` ties on insertion order only.

    Returns up to `n` `(phase, classification, count)` tuples, highest
    count first. With `n=1` this reproduces the old single-pattern
    behavior exactly."""

    def _latest_end_time(pattern: tuple[str, str]) -> str:
        phase, classification = pattern
        times = [
            m["end_time"]
            for m in all_flagged
            if m["phase"] == phase and m["classification"] == classification and m["end_time"]
        ]
        return max(times) if times else ""

    # Three stable sorts, least-important key first: recency (tertiary),
    # classification priority (secondary), count descending (primary).
    # Stable sort means each later sort only breaks ties within groups the
    # earlier sort already ordered.
    ranked = list(counts.items())
    ranked.sort(key=lambda item: _latest_end_time(item[0]), reverse=True)
    ranked.sort(key=lambda item: _CLASSIFICATION_PRIORITY.get(item[0][1], 99))
    ranked.sort(key=lambda item: item[1], reverse=True)

    return [(phase, classification, count) for (phase, classification), count in ranked[:n]]
```

Then in `aggregate_weakness_data`, replace this block:

```python
    top_phase, top_classification, top_count = _pick_top_pattern(counts, all_flagged)

    top_pattern_moves = [
        m
        for m in all_flagged
        if m["phase"] == top_phase and m["classification"] == top_classification
    ]
    # Newest game first (empty/None end_time sorts last).
    top_pattern_moves.sort(key=lambda m: m["end_time"] or "", reverse=True)

    counts_by_pattern = {
        f"{phase}:{classification}": count for (phase, classification), count in counts.items()
    }

    return {
        "total_games": len(games),
        "total_flagged": len(all_flagged),
        "counts_by_pattern": counts_by_pattern,
        "top_pattern": {
            "phase": top_phase,
            "classification": top_classification,
            "count": top_count,
        },
        "top_pattern_moves": top_pattern_moves,
        "affected_game_ids": affected_game_ids,
    }
```

with:

```python
    top_patterns_ranked = _pick_top_patterns(counts, all_flagged, n=MAX_TOP_PATTERNS)
    top_phase, top_classification, top_count = top_patterns_ranked[0]

    def _moves_for(phase: str, classification: str) -> list[dict[str, Any]]:
        moves = [
            m for m in all_flagged if m["phase"] == phase and m["classification"] == classification
        ]
        # Newest game first (empty/None end_time sorts last).
        moves.sort(key=lambda m: m["end_time"] or "", reverse=True)
        return moves

    top_pattern_moves = _moves_for(top_phase, top_classification)

    moves_by_pattern = {
        f"{phase}:{classification}": _moves_for(phase, classification)
        for phase, classification, _count in top_patterns_ranked
    }

    counts_by_pattern = {
        f"{phase}:{classification}": count for (phase, classification), count in counts.items()
    }

    return {
        "total_games": len(games),
        "total_flagged": len(all_flagged),
        "counts_by_pattern": counts_by_pattern,
        "top_pattern": {
            "phase": top_phase,
            "classification": top_classification,
            "count": top_count,
        },
        "top_pattern_moves": top_pattern_moves,
        "top_patterns": [
            {"phase": phase, "classification": classification, "count": count}
            for phase, classification, count in top_patterns_ranked
        ],
        "moves_by_pattern": moves_by_pattern,
        "affected_game_ids": affected_game_ids,
    }
```

And update the early-return branch (the `if not counts:` block) to also
include the two new empty keys:

```python
    if not counts:
        return {
            "total_games": len(games),
            "total_flagged": 0,
            "counts_by_pattern": {},
            "top_pattern": None,
            "top_pattern_moves": [],
            "top_patterns": [],
            "moves_by_pattern": {},
            "affected_game_ids": [],
        }
```

Finally, update the function's docstring to mention the two new return
keys (`top_patterns`, `moves_by_pattern`) alongside the existing ones.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_weakness_profile.py -v`
Expected: PASS (all tests, including the pre-existing ones — confirms
`top_pattern`/`top_pattern_moves` behavior is unchanged)

- [ ] **Step 5: Commit**

```bash
git add backend/app/weakness_profile.py backend/tests/test_weakness_profile.py
git commit -m "feat: rank top-3 weakness patterns instead of just the single top one"
```

---

## Task 3: Interleaved, re-queue-aware `extract_practice_positions`

**Files:**
- Modify: `backend/app/focus.py`
- Modify: `backend/app/routers/focus.py` (call site)
- Test: `backend/tests/test_focus.py`

**Interfaces:**
- Consumes: `aggregated_data["top_patterns"]` / `aggregated_data
  ["moves_by_pattern"]` from Task B (falls back to `top_pattern`/
  `top_pattern_moves` if those keys are absent, so callers passing an
  older-shaped dict still work). `PracticeAttempt` from Task A.
- Produces: `extract_practice_positions(games, aggregated_data, session=
  None, max_positions=PRACTICE_POSITIONS_MAX) -> dict[str, Any]` returning
  `{"positions": list[dict], "skipped_count": int}`. Each position dict
  now also carries `"game_id"`, `"move_number"`, `"side"` (needed by Task
  D/F to identify the puzzle back to the server). **This is a breaking
  change to the function's return shape** (previously a bare list) — the
  one existing call site (`routers/focus.py::_compute_daily_focus`) is
  updated in this task.

- [ ] **Step 1: Update existing tests for the new return shape**

In `backend/tests/test_focus.py`, update the three existing
`extract_practice_positions` tests to unwrap `["positions"]` and assert
the new identity fields:

```python
def test_extract_practice_positions_builds_fen_played_move_and_best_move():
    games = [_game(1)]
    aggregated = {
        "top_pattern_moves": [
            {
                "game_id": 1,
                "move_number": 3,
                "san": "Qxf6",
                "side": "white",
                "classification": "blunder",
                "phase": "opening",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
    }

    result = extract_practice_positions(games, aggregated)
    positions = result["positions"]

    assert result["skipped_count"] == 0
    assert len(positions) == 1
    position = positions[0]
    assert position["played_move"] == "Qxf6"
    assert position["best_move"] == "Qxf3"
    assert position["classification"] == "blunder"
    assert position["game_id"] == 1
    assert position["move_number"] == 3
    assert position["side"] == "white"
    # FEN should reflect the position right before White's move 3 (Qxf6):
    # White queen still on f3, not yet captured the knight on f6.
    assert " w " in position["fen"]


def test_extract_practice_positions_respects_max_and_skips_unknown_games():
    aggregated = {
        "top_pattern_moves": [
            {
                "game_id": 999,  # not in `games` -- should be skipped
                "move_number": 3,
                "san": "Qxf6",
                "side": "white",
                "classification": "blunder",
                "phase": "opening",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
        + [
            {
                "game_id": 1,
                "move_number": 3,
                "san": "Qxf6",
                "side": "white",
                "classification": "blunder",
                "phase": "opening",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
        * (PRACTICE_POSITIONS_MAX + 2)
    }
    games = [_game(1)]

    result = extract_practice_positions(games, aggregated)

    assert len(result["positions"]) == PRACTICE_POSITIONS_MAX


def test_extract_practice_positions_skips_moves_fen_before_move_cannot_resolve():
    games = [_game(1)]
    aggregated = {
        "top_pattern_moves": [
            {
                "game_id": 1,
                "move_number": 50,  # far beyond the short PGN's length
                "san": "Zzz",
                "side": "white",
                "classification": "blunder",
                "phase": "endgame",
                "eval_cp": -900,
                "best_move": "Qxf3",
            }
        ]
    }

    result = extract_practice_positions(games, aggregated)

    assert result["positions"] == []
    assert result["skipped_count"] == 1
```

Then add new tests for interleaving, re-queue, and session-aware
filtering at the end of the file:

```python
# --- extract_practice_positions: multi-pattern interleave + re-queue -----

from sqlmodel import Session, SQLModel, create_engine

from app.models import PracticeAttempt


def _move(game_id, move_number, san, phase, classification, end_time="2026-01-01T00:00:00+00:00"):
    return {
        "game_id": game_id,
        "end_time": end_time,
        "move_number": move_number,
        "san": san,
        "side": "white",
        "classification": classification,
        "phase": phase,
        "eval_cp": -900,
        "best_move": "Qxf3",
    }


MULTI_PATTERN_PGN = (
    "1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6 4. Nc3 d5 5. exd5 Qxd5 "
    "6. Nxd5 Rb8 7. Nc3 e5 8. Bc4 Be6 9. Bxe6 fxe6"
)


def _multi_game(game_id: int) -> Game:
    game = Game(
        chesscom_game_id=f"g{game_id}",
        pgn=MULTI_PATTERN_PGN,
        end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        time_class="blitz",
        result="win",
        source="chesscom",
        analyzed=True,
        user_color="white",
    )
    game.id = game_id
    return game


@pytest.fixture()
def attempt_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_extract_practice_positions_interleaves_across_top_patterns():
    games = [_multi_game(1)]
    aggregated = {
        "moves_by_pattern": {
            "opening:blunder": [_move(1, 3, "Qxf6", "opening", "blunder")],
            "middlegame:mistake": [_move(1, 5, "exd5", "middlegame", "mistake")],
        },
    }

    result = extract_practice_positions(games, aggregated, max_positions=2)

    classifications = [p["classification"] for p in result["positions"]]
    assert set(classifications) == {"blunder", "mistake"}


def test_extract_practice_positions_skips_solved_positions(attempt_session):
    games = [_multi_game(1)]
    attempt_session.add(
        PracticeAttempt(
            game_id=1,
            move_number=3,
            side="white",
            fen="irrelevant",
            solved=True,
            attempts_count=1,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    attempt_session.commit()

    aggregated = {
        "moves_by_pattern": {
            "opening:blunder": [_move(1, 3, "Qxf6", "opening", "blunder")],
        },
    }

    result = extract_practice_positions(games, aggregated, session=attempt_session)

    assert result["positions"] == []


def test_extract_practice_positions_requeues_open_wrong_attempts_first(attempt_session):
    games = [_multi_game(1)]
    # move_number=3 was answered incorrectly before (open, unsolved);
    # move_number=5 has never been attempted.
    attempt_session.add(
        PracticeAttempt(
            game_id=1,
            move_number=3,
            side="white",
            fen="irrelevant",
            solved=False,
            attempts_count=1,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    attempt_session.commit()

    aggregated = {
        "moves_by_pattern": {
            "middlegame:mistake": [_move(1, 5, "exd5", "middlegame", "mistake")],
            "opening:blunder": [_move(1, 3, "Qxf6", "opening", "blunder")],
        },
    }

    result = extract_practice_positions(games, aggregated, session=attempt_session, max_positions=1)

    assert len(result["positions"]) == 1
    assert result["positions"][0]["move_number"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_focus.py -v`
Expected: FAIL (return-shape mismatch on old tests, `TypeError`/`KeyError`
on the new session-aware ones)

- [ ] **Step 3: Rewrite `extract_practice_positions`**

In `backend/app/focus.py`, add imports:

```python
from sqlmodel import Session, select

from app.models import Game, PracticeAttempt
```

(`Game` is already imported — just add `PracticeAttempt` to that line;
add the new `sqlmodel` import above it.)

Replace the whole `extract_practice_positions` function with:

```python
def extract_practice_positions(
    games: list[Game],
    aggregated_data: dict[str, Any],
    session: Session | None = None,
    max_positions: int = PRACTICE_POSITIONS_MAX,
) -> dict[str, Any]:
    """Extract up to `max_positions` practice positions, interleaved
    round-robin across the ranked weakness patterns in
    `aggregated_data["moves_by_pattern"]` (falls back to the single
    `top_pattern`/`top_pattern_moves` pair if that key is absent, so
    older-shaped `aggregated_data` still works).

    If `session` is given, positions with a persisted `PracticeAttempt`
    row marked `solved=True` are excluded, and positions with an *open*
    attempt (`solved=False`, `attempts_count > 0` -- i.e. previously
    answered wrong) are moved to the front of the queue ahead of
    never-attempted positions, so incorrect puzzles resurface in a later
    session. Without a session, no filtering or reordering happens.

    Returns `{"positions": list[dict], "skipped_count": int}`. Each
    position dict has `fen`, `played_move`, `best_move`, `classification`,
    `game_id`, `move_number`, `side` -- the last three identify the puzzle
    back to the server for `POST /practice/check-move` attempt tracking.
    `skipped_count` counts flagged moves whose FEN reconstruction failed
    and were excluded, so callers can surface that instead of the
    position count just looking mysteriously short.
    """
    games_by_id = {game.id: game for game in games}

    moves_by_pattern = aggregated_data.get("moves_by_pattern")
    if not moves_by_pattern:
        top_pattern = aggregated_data.get("top_pattern")
        top_pattern_moves = aggregated_data.get("top_pattern_moves") or []
        if top_pattern and top_pattern_moves:
            key = f"{top_pattern['phase']}:{top_pattern['classification']}"
            moves_by_pattern = {key: top_pattern_moves}
        else:
            moves_by_pattern = {}

    # Round-robin interleave across patterns, in the patterns' ranked order.
    move_queues = [list(moves) for moves in moves_by_pattern.values()]
    interleaved: list[dict[str, Any]] = []
    while any(move_queues):
        for queue in move_queues:
            if queue:
                interleaved.append(queue.pop(0))

    if session is not None:
        game_ids = [g.id for g in games if g.id is not None]
        attempts_by_key: dict[tuple[int, int, str], PracticeAttempt] = {}
        if game_ids:
            rows = session.exec(
                select(PracticeAttempt).where(PracticeAttempt.game_id.in_(game_ids))
            ).all()
            attempts_by_key = {(row.game_id, row.move_number, row.side): row for row in rows}

        def _attempt_for(move: dict[str, Any]) -> PracticeAttempt | None:
            return attempts_by_key.get((move["game_id"], move["move_number"], move["side"]))

        def _is_solved(move: dict[str, Any]) -> bool:
            attempt = _attempt_for(move)
            return attempt is not None and attempt.solved

        def _is_open_retry(move: dict[str, Any]) -> bool:
            attempt = _attempt_for(move)
            return attempt is not None and not attempt.solved and attempt.attempts_count > 0

        interleaved = [m for m in interleaved if not _is_solved(m)]
        # Stable sort: open-retry moves move to the front, relative order
        # preserved within each group.
        interleaved.sort(key=lambda m: 0 if _is_open_retry(m) else 1)

    positions: list[dict[str, Any]] = []
    skipped_count = 0

    for move in interleaved:
        if len(positions) >= max_positions:
            break

        game = games_by_id.get(move["game_id"])
        if game is None:
            continue
        try:
            fen = fen_before_move(game.pgn, move["move_number"], move["side"])
        except RuntimeError:
            logger.exception(
                "Failed to derive FEN for practice position (game_id=%s, "
                "move_number=%s, side=%s); skipping this position.",
                move["game_id"],
                move["move_number"],
                move["side"],
            )
            skipped_count += 1
            continue

        positions.append(
            {
                "fen": fen,
                "played_move": move["san"],
                "best_move": move["best_move"],
                "classification": move["classification"],
                "game_id": move["game_id"],
                "move_number": move["move_number"],
                "side": move["side"],
            }
        )

    return {"positions": positions, "skipped_count": skipped_count}
```

- [ ] **Step 4: Update the call site in `routers/focus.py`**

In `backend/app/routers/focus.py`, in `_compute_daily_focus`, replace:

```python
            practice_positions = extract_practice_positions(games, aggregated)
```

with:

```python
            extraction = extract_practice_positions(
                games, aggregated, session=session, max_positions=PRACTICE_POSITIONS_MAX
            )
            practice_positions = extraction["positions"]
```

Add the import for `PRACTICE_POSITIONS_MAX` — change:

```python
from app.focus import extract_practice_positions, generate_daily_focus
```

to:

```python
from app.focus import PRACTICE_POSITIONS_MAX, extract_practice_positions, generate_daily_focus
```

Also add `game_id`, `move_number`, `side` to the `PracticePosition`
Pydantic model in the same file (needed so `GET /focus/today`'s response
carries the new fields too, and so Task D can import this same model):

```python
class PracticePosition(BaseModel):
    fen: str
    played_move: str
    best_move: str | None
    classification: str
    game_id: int
    move_number: int
    side: str
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_focus.py tests/test_focus_router.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/focus.py backend/app/routers/focus.py backend/tests/test_focus.py
git commit -m "feat: interleave practice positions across top patterns, re-queue wrong answers, report skipped positions"
```

---

## Task 4: On-demand puzzle endpoint + check-move attempt tracking

**Files:**
- Modify: `backend/app/routers/practice.py`
- Test: `backend/tests/test_practice.py`

**Interfaces:**
- Consumes: `extract_practice_positions` (Task C), `aggregate_weakness_data`
  (Task B), `PracticeAttempt` (Task A), `PracticePosition` schema (Task C,
  now in `app.routers.focus`).
- Produces: `GET /practice/positions` returning `PracticePositionsResponse`
  (`positions: list[PracticePosition]`, `skipped_count: int`,
  `solved_count: int`, `total_tracked: int`). `POST /practice/check-move`
  request body gains optional `game_id`, `move_number`, `side`; when all
  three are present, the attempt is persisted to `PracticeAttempt`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_practice.py`. This needs an isolated in-memory
DB like `test_focus_router.py` uses, since these tests touch
`PracticeAttempt` persistence — add these imports and fixtures near the
top of the file (alongside the existing ones):

```python
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.models import Game, PracticeAttempt

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

BLUNDER_ANALYSIS = [
    {
        "move_number": 3,
        "san": "Qxf6",
        "side": "white",
        "classification": "blunder",
        "phase": "opening",
        "eval_cp": -900,
        "best_move": "Nf3",
    },
]


def _seed_analyzed_game(session: Session, *, suffix: str, end_time: datetime) -> Game:
    game = Game(
        chesscom_game_id=f"g-{suffix}",
        pgn="1. e4 Nf6 2. Qf3 Nc6 3. Qxf6 gxf6",
        end_time=end_time,
        time_class="blitz",
        result="win",
        source="chesscom",
        analyzed=True,
        analysis_json=json.dumps(BLUNDER_ANALYSIS),
        user_color="white",
    )
    session.add(game)
    session.commit()
    session.refresh(game)
    return game


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_client(db_engine):
    def get_session_override():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

Then add the test cases at the end of the file:

```python
def test_get_practice_positions_returns_positions_from_recent_games(db_client, db_engine):
    with Session(db_engine) as session:
        for i in range(3):
            _seed_analyzed_game(session, suffix=str(i), end_time=BASE_TIME + timedelta(days=i))

    response = db_client.get("/practice/positions", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert len(body["positions"]) >= 1
    position = body["positions"][0]
    assert position["classification"] == "blunder"
    assert position["game_id"] is not None
    assert position["move_number"] == 3
    assert position["side"] == "white"
    assert body["skipped_count"] == 0
    assert body["total_tracked"] == 0
    assert body["solved_count"] == 0


def test_get_practice_positions_without_auth_returns_401(db_client):
    response = db_client.get("/practice/positions")
    assert response.status_code == 401


def test_check_move_records_attempt_when_position_identity_given(db_client, db_engine):
    with Session(db_engine) as session:
        game = _seed_analyzed_game(session, suffix="1", end_time=BASE_TIME)
        game_id = game.id

    response = db_client.post(
        "/practice/check-move",
        json={
            "fen": BEFORE_BLUNDER_FEN,
            "move_uci": "f3f6",
            "game_id": game_id,
            "move_number": 3,
            "side": "white",
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["correct"] is False

    with Session(db_engine) as session:
        attempt = session.exec(select(PracticeAttempt)).one()
        assert attempt.game_id == game_id
        assert attempt.move_number == 3
        assert attempt.side == "white"
        assert attempt.solved is False
        assert attempt.attempts_count == 1
        assert attempt.last_attempted_at is not None


def test_check_move_marks_attempt_solved_once_any_attempt_is_correct(db_client, db_engine):
    with Session(db_engine) as session:
        game = _seed_analyzed_game(session, suffix="1", end_time=BASE_TIME)
        game_id = game.id

    # First attempt: wrong.
    db_client.post(
        "/practice/check-move",
        json={
            "fen": BEFORE_BLUNDER_FEN,
            "move_uci": "f3f6",
            "game_id": game_id,
            "move_number": 3,
            "side": "white",
        },
        headers=AUTH_HEADERS,
    )

    # Second attempt: discover and play the actual best move.
    discovery = db_client.post(
        "/practice/check-move",
        json={"fen": BEFORE_BLUNDER_FEN, "move_uci": "f3f6"},
        headers=AUTH_HEADERS,
    ).json()
    best_move = discovery["best_move"]

    db_client.post(
        "/practice/check-move",
        json={
            "fen": BEFORE_BLUNDER_FEN,
            "move_uci": best_move,
            "game_id": game_id,
            "move_number": 3,
            "side": "white",
        },
        headers=AUTH_HEADERS,
    )

    with Session(db_engine) as session:
        attempt = session.exec(
            select(PracticeAttempt).where(
                PracticeAttempt.game_id == game_id,
                PracticeAttempt.move_number == 3,
                PracticeAttempt.side == "white",
            )
        ).one()
        assert attempt.solved is True
        assert attempt.attempts_count == 2


def test_check_move_without_position_identity_does_not_create_attempt(db_client, db_engine):
    response = db_client.post(
        "/practice/check-move",
        json={"fen": STARTING_FEN, "move_uci": "e2e4"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200

    with Session(db_engine) as session:
        assert session.exec(select(PracticeAttempt)).all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_practice.py -v`
Expected: FAIL (`404` on `GET /practice/positions`, no `PracticeAttempt`
rows created by `check-move`)

- [ ] **Step 3: Implement the router changes**

Replace the full contents of `backend/app/routers/practice.py` with:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.analysis_backfill import BACKFILL_LIMIT
from app.auth import require_auth
from app.chess_engine import check_move
from app.db import get_session
from app.focus import PRACTICE_POSITIONS_MAX, extract_practice_positions
from app.models import Game, PracticeAttempt
from app.routers.focus import PracticePosition
from app.weakness_profile import aggregate_weakness_data

router = APIRouter(prefix="/practice", tags=["practice"], dependencies=[Depends(require_auth)])


class CheckMoveRequest(BaseModel):
    fen: str
    move_uci: str
    game_id: int | None = None
    move_number: int | None = None
    side: str | None = None


class CheckMoveResponse(BaseModel):
    correct: bool
    best_move: str | None
    played_eval_cp: int


class PracticePositionsResponse(BaseModel):
    positions: list[PracticePosition]
    skipped_count: int
    solved_count: int
    total_tracked: int


@router.post("/check-move", response_model=CheckMoveResponse)
def check_move_endpoint(
    body: CheckMoveRequest, session: Session = Depends(get_session)
) -> CheckMoveResponse:
    try:
        result = check_move(body.fen, body.move_uci)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    if body.game_id is not None and body.move_number is not None and body.side is not None:
        _record_attempt(
            session, body.game_id, body.move_number, body.side, body.fen, result["correct"]
        )

    return CheckMoveResponse(**result)


def _record_attempt(
    session: Session, game_id: int, move_number: int, side: str, fen: str, correct: bool
) -> None:
    """Upsert the `PracticeAttempt` row for one puzzle: increment the
    attempt count, and mark it solved as soon as any attempt is correct
    (a puzzle stays solved even if a later attempt at the same position is
    wrong -- `solved` only ever moves False -> True, never back)."""
    attempt = session.exec(
        select(PracticeAttempt).where(
            PracticeAttempt.game_id == game_id,
            PracticeAttempt.move_number == move_number,
            PracticeAttempt.side == side,
        )
    ).first()
    now = datetime.now(timezone.utc)

    if attempt is None:
        attempt = PracticeAttempt(
            game_id=game_id,
            move_number=move_number,
            side=side,
            fen=fen,
            solved=correct,
            attempts_count=1,
            last_attempted_at=now,
            created_at=now,
        )
    else:
        attempt.attempts_count += 1
        attempt.solved = attempt.solved or correct
        attempt.last_attempted_at = now

    session.add(attempt)
    session.commit()


@router.get("/positions", response_model=PracticePositionsResponse)
def get_practice_positions(session: Session = Depends(get_session)) -> PracticePositionsResponse:
    """Generate a fresh practice puzzle set on demand, independent of the
    once-a-day cached `DailyFocus` text -- lets the user train more than
    once a day without waiting for a new focus computation."""
    games = session.exec(
        select(Game)
        .where(Game.analyzed == True)  # noqa: E712
        .order_by(Game.end_time.desc())
        .limit(BACKFILL_LIMIT)
    ).all()

    aggregated = aggregate_weakness_data(games)
    extraction = extract_practice_positions(
        games, aggregated, session=session, max_positions=PRACTICE_POSITIONS_MAX
    )

    total_tracked = session.exec(select(func.count()).select_from(PracticeAttempt)).one()
    solved_count = session.exec(
        select(func.count())
        .select_from(PracticeAttempt)
        .where(PracticeAttempt.solved == True)  # noqa: E712
    ).one()

    return PracticePositionsResponse(
        positions=[PracticePosition(**p) for p in extraction["positions"]],
        skipped_count=extraction["skipped_count"],
        solved_count=solved_count,
        total_tracked=total_tracked,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_practice.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no regressions (baseline was 121 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/practice.py backend/tests/test_practice.py
git commit -m "feat: add GET /practice/positions on-demand endpoint and check-move attempt tracking"
```

---

## Task 5: Frontend — on-demand puzzle sets, progress, skipped-puzzle banner

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/PracticePanel.tsx`

**Interfaces:**
- Consumes: `GET /practice/positions` and the extended
  `POST /practice/check-move` body from Task D.
- Produces: `getPracticePositions()`, and an extended
  `checkPracticeMove(fen, moveUci, position?)` in `api.ts` for later
  manual/browser testing (Task F) to exercise.

- [ ] **Step 1: Extend `api.ts`**

In `frontend/src/api.ts`, replace the existing `PracticePosition`
interface with (adds the three identity fields the backend now returns):

```typescript
export interface PracticePosition {
  fen: string;
  played_move: string;
  best_move: string | null;
  classification: string;
  game_id: number;
  move_number: number;
  side: "white" | "black";
}
```

Add a new interface and function after the existing `DailyFocus`-related
code (near `getDailyFocus`):

```typescript
export interface PracticePositionsResult {
  positions: PracticePosition[];
  skipped_count: number;
  solved_count: number;
  total_tracked: number;
}

export function getPracticePositions(): Promise<PracticePositionsResult> {
  return request<PracticePositionsResult>("/practice/positions", {
    method: "GET",
  });
}
```

Replace `checkPracticeMove` with a version that accepts optional puzzle
identity:

```typescript
export function checkPracticeMove(
  fen: string,
  moveUci: string,
  position?: { game_id: number; move_number: number; side: "white" | "black" }
): Promise<CheckMoveResult> {
  return request<CheckMoveResult>("/practice/check-move", {
    method: "POST",
    body: JSON.stringify({
      fen,
      move_uci: moveUci,
      ...(position
        ? {
            game_id: position.game_id,
            move_number: position.move_number,
            side: position.side,
          }
        : {}),
    }),
  });
}
```

- [ ] **Step 2: Rewrite `PracticeBoard` and `PracticePanel` in `PracticePanel.tsx`**

Replace the imports at the top of `frontend/src/PracticePanel.tsx`:

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { Chess } from "chess.js";
import { Chessboard, type PieceDropHandlerArgs } from "react-chessboard";
import {
  ApiError,
  checkPracticeMove,
  getDailyFocus,
  getPracticePositions,
  type CheckMoveResult,
  type DailyFocus,
  type PracticePosition,
  type PracticePositionsResult,
} from "./api";
```

Keep `DailyFocusCard` unchanged. Replace `PracticeBoard` entirely with a
version that takes a `positions` array directly (not `focus`), tracks
puzzle identity for `check-move`, and requests a new set when the current
one runs out:

```typescript
function PracticeBoard({
  positions,
  onSolvedCorrectly,
  onRequestNewSet,
  onUnauthorized,
}: {
  positions: PracticePosition[];
  onSolvedCorrectly: () => void;
  onRequestNewSet: () => void;
  onUnauthorized: () => void;
}) {
  const [index, setIndex] = useState(0);
  const chessRef = useRef(new Chess(positions[0].fen));
  const [fen, setFen] = useState(chessRef.current.fen());
  const [feedback, setFeedback] = useState<MoveFeedback>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);

  const loadPosition = useCallback((posIndex: number) => {
    const position = positions[posIndex];
    chessRef.current = new Chess(position.fen);
    setFen(chessRef.current.fen());
    setFeedback({ state: "idle" });
    setError(null);
  }, [positions]);

  const handleNextPosition = useCallback(() => {
    if (index + 1 >= positions.length) {
      onRequestNewSet();
      setIndex(0);
      return;
    }
    const nextIndex = index + 1;
    setIndex(nextIndex);
    loadPosition(nextIndex);
  }, [index, positions.length, loadPosition, onRequestNewSet]);

  const onPieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs): boolean => {
      if (!targetSquare || feedback.state !== "idle") return false;

      const startFen = chessRef.current.fen();
      const chess = chessRef.current;
      let move;
      try {
        move = chess.move({
          from: sourceSquare,
          to: targetSquare,
          promotion: "q",
        });
      } catch {
        return false;
      }
      if (!move) return false;

      setFen(chess.fen());

      const promotion = move.promotion ?? "";
      const moveUci = `${sourceSquare}${targetSquare}${promotion}`;
      const position = positions[index];

      setFeedback({ state: "checking" });
      setError(null);

      void (async () => {
        try {
          const result: CheckMoveResult = await checkPracticeMove(
            startFen,
            moveUci,
            {
              game_id: position.game_id,
              move_number: position.move_number,
              side: position.side,
            }
          );
          const bestMoveSan = result.best_move
            ? uciToSan(startFen, result.best_move)
            : null;
          setFeedback({
            state: "result",
            correct: result.correct,
            bestMoveSan,
          });
          if (result.correct) onSolvedCorrectly();
        } catch (err) {
          if (err instanceof ApiError && err.kind === "unauthorized") {
            onUnauthorized();
            return;
          }
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to check that move."
          );
          chessRef.current = new Chess(startFen);
          setFen(chessRef.current.fen());
          setFeedback({ state: "idle" });
        }
      })();

      return true;
    },
    [feedback.state, index, positions, onUnauthorized, onSolvedCorrectly]
  );

  const position = positions[index];

  return (
    <div className="practice-board-panel">
      <p className="muted practice-position-meta">
        Position {index + 1} of {positions.length} — your{" "}
        {position.classification} ({position.played_move})
      </p>

      <div className="board-container">
        <Chessboard
          options={{
            position: fen,
            onPieceDrop,
            id: "practice-board",
            allowDragging: feedback.state === "idle",
          }}
        />
      </div>

      <div
        className={`status-bar${
          feedback.state === "result"
            ? feedback.correct
              ? " status-done"
              : " status-error"
            : ""
        }`}
      >
        <span className="status-dot" />
        <span className="status-text">
          {feedback.state === "checking"
            ? "Checking move..."
            : feedback.state === "result"
              ? feedback.correct
                ? "Correct — that was the best move."
                : feedback.bestMoveSan
                  ? `Not quite — Stockfish preferred ${feedback.bestMoveSan}.`
                  : "Not quite the best move."
              : "Find the best move for this position."}
        </span>
      </div>

      {error && <p className="sync-error">{error}</p>}

      <div className="play-controls">
        <button type="button" onClick={handleNextPosition} disabled={feedback.state === "checking"}>
          Next position
        </button>
      </div>
    </div>
  );
}
```

Now replace the `PracticePanel` function with a version that fetches
practice positions from the new endpoint (separately from the daily
focus text), and renders progress + a skipped-puzzle banner:

```typescript
function PracticePanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [focus, setFocus] = useState<DailyFocus | null>(null);
  const [focusError, setFocusError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [practice, setPractice] = useState<PracticePositionsResult | null>(null);
  const [practiceError, setPracticeError] = useState<string | null>(null);

  const loadPracticePositions = useCallback(async () => {
    try {
      const result = await getPracticePositions();
      setPractice(result);
      setPracticeError(null);
    } catch (err) {
      if (err instanceof ApiError && err.kind === "unauthorized") {
        onUnauthorized();
        return;
      }
      setPracticeError(
        err instanceof ApiError ? err.message : "Failed to load practice puzzles."
      );
    }
  }, [onUnauthorized]);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const result = await getDailyFocus();
        if (cancelled) return;
        setFocus(result);
        setFocusError(null);
        if (result.status === "computing") {
          pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
        } else if (result.status === "ready") {
          void loadPracticePositions();
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.kind === "unauthorized") {
          onUnauthorized();
          return;
        }
        setFocusError(
          err instanceof ApiError ? err.message : "Failed to load today's focus."
        );
      }
    };

    void poll();

    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [onUnauthorized, loadPracticePositions]);

  const handleSolvedCorrectly = useCallback(() => {
    setPractice((prev) =>
      prev ? { ...prev, solved_count: prev.solved_count + 1 } : prev
    );
  }, []);

  if (focusError) {
    return <p className="sync-error">{focusError}</p>;
  }

  if (!focus || focus.status === "computing") {
    const total = focus?.progress_total ?? 0;
    const current = focus?.progress_current ?? 0;
    const hasProgress = total > 0;
    const percent = hasProgress
      ? Math.min(100, Math.round((current / total) * 100))
      : 0;

    return (
      <div className="status-bar-column">
        <div className="status-bar">
          <span className="status-dot" />
          <span className="status-text">
            {hasProgress
              ? `Analyzing your recent games... (${current} of ${total})`
              : "Getting started..."}
          </span>
        </div>
        {hasProgress && (
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
          </div>
        )}
      </div>
    );
  }

  if (focus.status === "insufficient_data") {
    return (
      <p className="muted">
        Not enough analyzed games yet to build a daily focus. Sync and play
        (or import) a few more games, then check back here.
      </p>
    );
  }

  if (focus.status === "error") {
    return (
      <p className="sync-error">
        Something went wrong computing today's focus. Please try again
        later.
      </p>
    );
  }

  return (
    <div className="practice-panel">
      <DailyFocusCard focus={focus} />

      {practiceError && <p className="sync-error">{practiceError}</p>}

      {practice && practice.total_tracked > 0 && (
        <p className="muted practice-progress">
          Solved {practice.solved_count} of {practice.total_tracked} tracked puzzles
        </p>
      )}

      {practice && practice.skipped_count > 0 && (
        <p className="sync-error">
          {practice.skipped_count} puzzle
          {practice.skipped_count === 1 ? "" : "s"} couldn't be loaded from
          your games and were skipped.
        </p>
      )}

      {practice && practice.positions.length > 0 ? (
        <PracticeBoard
          positions={practice.positions}
          onSolvedCorrectly={handleSolvedCorrectly}
          onRequestNewSet={() => void loadPracticePositions()}
          onUnauthorized={onUnauthorized}
        />
      ) : practice ? (
        <p className="muted">No practice positions available right now.</p>
      ) : (
        <p className="muted">Loading practice puzzles...</p>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run build`
Expected: builds without TypeScript errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts frontend/src/PracticePanel.tsx
git commit -m "feat: fetch practice puzzles on demand with progress and skipped-puzzle feedback"
```

---

## Task 6: Manual browser verification

**Files:** none (verification only)

- [ ] **Step 1: Start both servers**

```bash
cd backend && uv run fastapi dev app/main.py
```
```bash
cd frontend && npm run dev
```

- [ ] **Step 2: Walk the full practice flow in the browser**

1. Open the app, authenticate with the shared-secret token, go to the
   Practice tab.
2. Confirm the daily focus card still appears once, and a puzzle set
   loads (may take a moment on first load if `GET /focus/today` is still
   computing — practice positions only load once `status === "ready"`).
3. Solve a puzzle incorrectly, note which puzzle it was (played move /
   classification shown above the board), click through "Next position"
   until a full set is exhausted, and confirm a new set loads
   automatically instead of looping the same positions.
4. Keep clicking "Next position" across sets until the same puzzle you
   got wrong resurfaces — confirm it does show up again (re-queue
   working), and solve it correctly this time.
5. Confirm the "Solved X of Y tracked puzzles" line increments after a
   correct answer.
6. If you have games with no phase field pre-dating recent changes (or
   can force a bad move-number), verify the skipped-puzzle banner
   ("N puzzles couldn't be loaded...") appears when applicable — otherwise
   confirm it simply doesn't render when `skipped_count === 0`.

- [ ] **Step 3: Report results**

Confirm in the PR/summary that all six checks above passed, or note which
didn't and why.

---

## Verification (full branch)

- `cd backend && uv run pytest -q` — all tests pass, no regressions from
  the 121-test baseline captured before this work started.
- `cd frontend && npm run build` — typechecks clean.
- Task F's manual browser walkthrough completed.

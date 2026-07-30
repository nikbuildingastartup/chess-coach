"""Regression tests for the startup migration in app.db.

The scenario under test: a real, pre-existing `chess_coach.db` created by
an earlier version of this app (before the "played games" feature branch)
gets opened by the new code. `SQLModel.metadata.create_all()` alone does
NOT add new columns to an already-existing table and does NOT relax a
NOT NULL constraint -- so without an explicit migration, the new code
would crash with `sqlite3.OperationalError: no such column: game.source`
(or later, an IntegrityError when trying to insert a played game with a
NULL chesscom_game_id) against a real user's database.

We reproduce that by building a real on-disk SQLite file using the exact
pre-branch schema (see commit 30aef76: `chesscom_game_id: str =
Field(unique=True, index=True)`, no `source`/`analysis_json` columns),
seeding it with a row, then running the migration and asserting the
database ends up usable under the current schema -- deliberately NOT
using a fresh in-memory `create_all()` database, since that trivially
"passes" without ever exercising the migration path.

Critically, the old schema's `chesscom_game_id` column is declared
*without* an inline `UNIQUE` on the column -- SQLModel's
`Field(unique=True, index=True)` compiles to a plain `NOT NULL` column
plus a separate, *named* `CREATE UNIQUE INDEX ix_game_chesscom_game_id
ON game (chesscom_game_id)` statement (verified by constructing the real
pre-branch `Game` model and inspecting `PRAGMA index_list`: it reports
`(seq=0, name='ix_game_chesscom_game_id', unique=1, origin='c', ...)`).
An inline `chesscom_game_id TEXT UNIQUE` column constraint, by contrast,
produces an *unnamed autoindex* with origin `"u"` -- a different code
path in SQLite that does NOT collide with the new table's index the way
the real named index does. Using the inline form here would make this
test pass regardless of whether the migration's index-collision handling
(`_rebuild_game_table_with_nullable_chesscom_id`'s `DROP INDEX` loop over
origin `"c"` indexes) is present, giving false confidence. The
`CREATE UNIQUE INDEX` statement below is required to actually exercise
that fix.
"""

import sqlite3

from sqlalchemy import create_engine, text

import pytest

from app.db import _check_no_interrupted_migration, _migrate_game_table
from app.models import Game

OLD_SCHEMA_SQL = """
CREATE TABLE game (
    id INTEGER PRIMARY KEY,
    chesscom_game_id TEXT NOT NULL,
    pgn TEXT NOT NULL,
    end_time DATETIME NOT NULL,
    time_class TEXT NOT NULL,
    result TEXT NOT NULL,
    analyzed BOOLEAN NOT NULL
)
"""

OLD_SCHEMA_INDEX_SQL = """
CREATE UNIQUE INDEX ix_game_chesscom_game_id ON game (chesscom_game_id)
"""


def _make_pre_branch_db(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(OLD_SCHEMA_SQL)
        conn.execute(OLD_SCHEMA_INDEX_SQL)
        conn.execute(
            "INSERT INTO game "
            "(chesscom_game_id, pgn, end_time, time_class, result, analyzed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "https://www.chess.com/game/live/12345",
                "1. e4 e5",
                "2024-01-01 00:00:00+00:00",
                "blitz",
                "win",
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_pre_branch_fixture_reproduces_a_named_unique_index(tmp_path):
    """Sanity check on the fixture itself: the pre-branch schema must
    produce a NAMED index (origin "c"), matching what SQLModel's
    `Field(unique=True, index=True)` really generates -- not an unnamed
    autoindex (origin "u"), which an inline `UNIQUE` column constraint
    would produce instead and which does not exercise the index-collision
    fix under test below."""
    db_path = tmp_path / "fixture_check.db"
    _make_pre_branch_db(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        indexes = conn.execute("PRAGMA index_list(game)").fetchall()
    finally:
        conn.close()

    assert len(indexes) == 1
    _, name, unique, origin = indexes[0][:4]
    assert name == "ix_game_chesscom_game_id"
    assert unique == 1
    assert origin == "c"


def test_migration_upgrades_a_real_pre_branch_sqlite_file(tmp_path):
    db_path = tmp_path / "pre_branch_chess_coach.db"
    _make_pre_branch_db(str(db_path))

    migration_engine = create_engine(f"sqlite:///{db_path}")
    with migration_engine.begin() as conn:
        _migrate_game_table(conn)

    with migration_engine.begin() as conn:
        cols = {row[1]: row for row in conn.exec_driver_sql("PRAGMA table_info(game)").fetchall()}

        # New columns exist.
        assert "source" in cols
        assert "analysis_json" in cols
        assert "coaching_summary" in cols

        # chesscom_game_id is no longer NOT NULL.
        assert cols["chesscom_game_id"][3] == 0  # notnull flag

        # The pre-existing row survived the migration intact, backfilled
        # as a Chess.com import with no analysis.
        row = conn.execute(
            text(
                "SELECT chesscom_game_id, source, analysis_json, pgn, result "
                "FROM game WHERE id = 1"
            )
        ).one()
        assert row.chesscom_game_id == "https://www.chess.com/game/live/12345"
        assert row.source == "chesscom"
        assert row.analysis_json is None
        assert row.pgn == "1. e4 e5"
        assert row.result == "win"

        # A played game (NULL chesscom_game_id) can now be inserted, which
        # would have violated the old NOT NULL constraint.
        conn.execute(
            text(
                "INSERT INTO game "
                "(chesscom_game_id, pgn, end_time, time_class, result, source, analysis_json, "
                "analyzed, coaching_summary) "
                "VALUES (NULL, '1. e4', '2024-01-02 00:00:00+00:00', 'untimed', 'loss', 'played', "
                "'[]', 1, 'Nice game overall.')"
            )
        )


def test_migration_is_a_no_op_against_a_fresh_current_schema_db(tmp_path):
    """Running the migration against a database that already has the
    current schema (e.g. one `create_all` just made from scratch) must not
    raise and must not change anything."""
    from sqlmodel import SQLModel

    db_path = tmp_path / "fresh.db"
    fresh_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(fresh_engine)

    with fresh_engine.begin() as conn:
        _migrate_game_table(conn)  # must not raise

    with fresh_engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(game)").fetchall()}
        assert cols == {
            "id",
            "chesscom_game_id",
            "pgn",
            "end_time",
            "time_class",
            "result",
            "source",
            "analysis_json",
            "analyzed",
            "coaching_summary",
        }


def test_migration_no_op_when_table_does_not_exist(tmp_path):
    db_path = tmp_path / "empty.db"
    empty_engine = create_engine(f"sqlite:///{db_path}")
    with empty_engine.begin() as conn:
        _migrate_game_table(conn)  # must not raise on a DB with no tables at all


def test_startup_refuses_when_game_old_exists_from_an_interrupted_migration(tmp_path):
    """Simulates a process kill between `ALTER TABLE game RENAME TO
    game_old` and the rest of `_rebuild_game_table_with_nullable_chesscom_id`
    completing: `game_old` holds the user's real (pre-branch) data, and a
    fresh, empty `game` table has already been created by `create_all` on
    a subsequent, also-interrupted-looking startup.

    Without a guard, the app would boot fine and just show an empty games
    list -- indistinguishable from silent data loss. The startup check
    must refuse to proceed instead.
    """
    db_path = tmp_path / "interrupted.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # `game_old`: the orphaned original data, using the real pre-branch
        # shape (see the fixture above).
        conn.execute(OLD_SCHEMA_SQL.replace("CREATE TABLE game", "CREATE TABLE game_old"))
        conn.execute(
            OLD_SCHEMA_INDEX_SQL.replace(
                "ix_game_chesscom_game_id ON game", "ix_game_old_chesscom_game_id ON game_old"
            )
        )
        conn.execute(
            "INSERT INTO game_old "
            "(chesscom_game_id, pgn, end_time, time_class, result, analyzed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "https://www.chess.com/game/live/99999",
                "1. d4 d5",
                "2024-01-01 00:00:00+00:00",
                "blitz",
                "win",
                0,
            ),
        )
        # `game`: a fresh, empty table -- as `create_all` would leave it
        # on a subsequent startup, since the original `game` no longer
        # exists to block `CREATE TABLE IF NOT EXISTS`.
        conn.execute(
            "CREATE TABLE game ("
            "id INTEGER PRIMARY KEY, chesscom_game_id TEXT, pgn TEXT NOT NULL, "
            "end_time DATETIME NOT NULL, time_class TEXT NOT NULL, result TEXT NOT NULL, "
            "source TEXT NOT NULL, analysis_json TEXT, analyzed BOOLEAN NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    check_engine = create_engine(f"sqlite:///{db_path}")
    with check_engine.begin() as conn:
        with pytest.raises(RuntimeError, match="game_old"):
            _check_no_interrupted_migration(conn)


def test_startup_check_proceeds_when_no_game_old_table_exists(tmp_path):
    """The normal case -- no interrupted migration -- must not raise,
    whether there's no `game` table yet, an already-current `game` table,
    or a pre-branch `game` table awaiting migration."""
    db_path = tmp_path / "normal.db"
    engine_ = create_engine(f"sqlite:///{db_path}")

    with engine_.begin() as conn:
        _check_no_interrupted_migration(conn)  # empty DB: must not raise

    _make_pre_branch_db(str(db_path))
    with engine_.begin() as conn:
        _check_no_interrupted_migration(conn)  # pre-branch `game`, no `game_old`: must not raise

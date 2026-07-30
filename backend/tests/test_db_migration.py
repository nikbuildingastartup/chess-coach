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
pre-branch schema (see commit 30aef76: `chesscom_game_id TEXT NOT NULL
UNIQUE`, no `source`/`analysis_json` columns), seeding it with a row, then
running the migration and asserting the database ends up usable under the
current schema -- deliberately NOT using a fresh in-memory `create_all()`
database, since that trivially "passes" without ever exercising the
migration path.
"""

import sqlite3

from sqlalchemy import create_engine, text

from app.db import _migrate_game_table
from app.models import Game

OLD_SCHEMA_SQL = """
CREATE TABLE game (
    id INTEGER PRIMARY KEY,
    chesscom_game_id TEXT NOT NULL UNIQUE,
    pgn TEXT NOT NULL,
    end_time DATETIME NOT NULL,
    time_class TEXT NOT NULL,
    result TEXT NOT NULL,
    analyzed BOOLEAN NOT NULL
)
"""


def _make_pre_branch_db(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(OLD_SCHEMA_SQL)
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
                "(chesscom_game_id, pgn, end_time, time_class, result, source, analysis_json, analyzed) "
                "VALUES (NULL, '1. e4', '2024-01-02 00:00:00+00:00', 'untimed', 'loss', 'played', '[]', 1)"
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
        }


def test_migration_no_op_when_table_does_not_exist(tmp_path):
    db_path = tmp_path / "empty.db"
    empty_engine = create_engine(f"sqlite:///{db_path}")
    with empty_engine.begin() as conn:
        _migrate_game_table(conn)  # must not raise on a DB with no tables at all

from collections.abc import Generator

from sqlalchemy import Connection
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def _migrate_game_table(conn: Connection) -> None:
    """Idempotently bring an existing `game` table up to this branch's schema.

    `SQLModel.metadata.create_all()` only issues `CREATE TABLE IF NOT
    EXISTS` — it never alters a table that already exists. This branch
    added two new columns to `Game` (`source` NOT NULL, `analysis_json`
    nullable) and relaxed `chesscom_game_id` from NOT NULL to nullable.
    Against a database created before this branch (e.g. a user's existing
    `chess_coach.db` full of Chess.com imports), none of that happens
    automatically — this function does it by hand.

    Safe to call on every startup, including against a brand-new database
    that `create_all` just created with the current schema: every step
    below checks `PRAGMA table_info` first and is a no-op if the column
    already matches.
    """
    rows = conn.exec_driver_sql("PRAGMA table_info(game)").fetchall()
    if not rows:
        # No `game` table at all -- either create_all hasn't run yet, or
        # this is a fresh DB about to get the current schema. Nothing to
        # migrate either way.
        return

    columns = {row[1]: row for row in rows}  # name -> (cid, name, type, notnull, dflt_value, pk)

    if "source" not in columns:
        conn.exec_driver_sql(
            "ALTER TABLE game ADD COLUMN source VARCHAR NOT NULL DEFAULT 'chesscom'"
        )

    if "analysis_json" not in columns:
        conn.exec_driver_sql("ALTER TABLE game ADD COLUMN analysis_json VARCHAR")

    # Re-check chesscom_game_id's nullability after any ADD COLUMNs above
    # (those don't affect it, but re-reading keeps this self-contained).
    columns = {
        row[1]: row for row in conn.exec_driver_sql("PRAGMA table_info(game)").fetchall()
    }
    chesscom_col = columns.get("chesscom_game_id")
    if chesscom_col is not None and chesscom_col[3] == 1:  # notnull flag == 1
        _rebuild_game_table_with_nullable_chesscom_id(conn)


def _rebuild_game_table_with_nullable_chesscom_id(conn: Connection) -> None:
    """Relax `chesscom_game_id` from NOT NULL to nullable.

    SQLite has no `ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL`. The
    standard workaround is: rename the old table out of the way, create a
    new table with the desired (current) schema in its place, copy every
    row across, then drop the renamed-away original.
    """
    conn.exec_driver_sql("ALTER TABLE game RENAME TO game_old")

    # SQLite index names are global to the schema, not scoped to their
    # table -- renaming the table above does NOT rename its indexes, so
    # the old `ix_game_chesscom_game_id` index (now pointing at
    # `game_old`) would collide with the identically-named index the new
    # `game` table creation is about to define. Drop the old indexes
    # first to make room.
    old_indexes = conn.exec_driver_sql("PRAGMA index_list(game_old)").fetchall()
    for idx in old_indexes:
        index_name, origin = idx[1], idx[3]
        # Auto-generated indexes backing a UNIQUE/PK constraint (origin
        # "u"/"pk") are owned by the table and dropped automatically when
        # it is; SQLite refuses an explicit DROP INDEX on those. Only
        # explicitly-created indexes (origin "c", e.g. SQLModel's
        # `index=True` columns) need dropping here.
        if origin == "c":
            conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{index_name}"')

    SQLModel.metadata.tables["game"].create(conn)
    conn.exec_driver_sql(
        """
        INSERT INTO game (
            id, chesscom_game_id, pgn, end_time, time_class, result,
            source, analysis_json, analyzed
        )
        SELECT
            id, chesscom_game_id, pgn, end_time, time_class, result,
            source, analysis_json, analyzed
        FROM game_old
        """
    )
    conn.exec_driver_sql("DROP TABLE game_old")


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            _migrate_game_table(conn)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

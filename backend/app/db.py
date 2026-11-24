"""SQLite engine and session.

SQLite lives on the App Service /home share (Azure Files over SMB), so:
- no WAL mode (WAL is unsafe on network file systems); default rollback journal
- busy_timeout so concurrent writers wait instead of failing
- one app instance and one worker process (see startup.sh)
"""
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    db_path = Path(get_settings().db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all only creates missing tables. Add columns introduced after a table was created."""
    added = {
        ("attachments", "category"): "VARCHAR(20) NOT NULL DEFAULT 'supporting'",
        ("requests", "capital_cost_center"): "VARCHAR(4)",
        ("requests", "legal_review"): "BOOLEAN",
        ("requests", "po_number"): "VARCHAR(50)",
        ("requests", "po_issued_at"): "DATETIME",
        ("requests", "po_issued_by"): "VARCHAR(320)",
        ("requests", "po_email_status"): "VARCHAR(10)",
        ("requests", "po_email_error"): "TEXT",
        ("requests", "po_email_sent_at"): "DATETIME",
    }
    with engine.begin() as conn:
        for (table, column), ddl in added.items():
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

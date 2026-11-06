"""Persist workflow attempts and outcomes in a local audit table."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    inspect,
)
from sqlalchemy.engine import Engine, URL

from askdb.seed import DATABASE_PATH


_metadata = MetaData()
_audit_log = Table(
    "audit_log",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "timestamp",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    Column("question", Text, nullable=False),
    Column("sql", Text, nullable=False),
    Column("role", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("reason", Text),
    Column("attempts", Integer, nullable=False, server_default="1"),
    CheckConstraint("status IN ('allowed', 'blocked')", name="ck_audit_status"),
)


@lru_cache(maxsize=4)
def _audit_engine(database_path: Path) -> Engine:
    """Create the local writable engine used only for audit events."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite", database=str(database_path.resolve()))
    engine = create_engine(url)
    _metadata.create_all(engine)
    if "attempts" not in {
        column["name"] for column in inspect(engine).get_columns("audit_log")
    }:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE audit_log "
                "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1"
            )
    return engine


def log_question(
    question: str,
    sql: str,
    role: str,
    status: Literal["allowed", "blocked"],
    reason: str = "",
    database_path: Path = DATABASE_PATH,
    attempts: int = 1,
) -> None:
    """Append one workflow outcome, including its number of SQL attempts."""
    if attempts < 1:
        raise ValueError("attempts must be greater than zero.")
    with _audit_engine(database_path).begin() as connection:
        connection.execute(
            _audit_log.insert().values(
                question=question,
                sql=sql,
                role=role,
                status=status,
                reason=reason,
                attempts=attempts,
            )
        )

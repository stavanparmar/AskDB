"""Persist submitted questions and permission outcomes in a local audit table."""

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
    CheckConstraint("status IN ('allowed', 'blocked')", name="ck_audit_status"),
)


@lru_cache(maxsize=4)
def _audit_engine(database_path: Path) -> Engine:
    """Create the local writable engine used only for audit events."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite", database=str(database_path.resolve()))
    engine = create_engine(url)
    _metadata.create_all(engine)
    return engine


def log_question(
    question: str,
    sql: str,
    role: str,
    status: Literal["allowed", "blocked"],
    reason: str = "",
    database_path: Path = DATABASE_PATH,
) -> None:
    """Append one question and its authorization outcome to the audit table."""
    with _audit_engine(database_path).begin() as connection:
        connection.execute(
            _audit_log.insert().values(
                question=question,
                sql=sql,
                role=role,
                status=status,
                reason=reason,
            )
        )

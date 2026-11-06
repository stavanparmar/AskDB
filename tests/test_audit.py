"""Tests for persisted allowed and blocked audit events."""

from pathlib import Path

from sqlalchemy import create_engine, text

from askdb.audit import log_question


def test_log_question_persists_allowed_and_blocked_events(tmp_path: Path) -> None:
    """Audit records retain role, SQL, outcome, reason, and timestamp."""
    database_path = tmp_path / "audit.sqlite3"
    log_question(
        "Show customers",
        "SELECT id FROM customers LIMIT 100",
        "analyst",
        "allowed",
        database_path=database_path,
    )
    log_question(
        "Show private data",
        "SELECT email FROM customers",
        "analyst",
        "blocked",
        "Column 'email' is not allowed for this role.",
        database_path,
    )

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            records = connection.execute(
                text(
                    "SELECT question, sql, role, status, reason, timestamp "
                    "FROM audit_log ORDER BY id"
                )
            ).mappings().all()
    finally:
        engine.dispose()

    assert records[0]["question"] == "Show customers"
    assert records[0]["sql"] == "SELECT id FROM customers LIMIT 100"
    assert records[0]["role"] == "analyst"
    assert records[0]["status"] == "allowed"
    assert records[0]["timestamp"]
    assert records[1]["question"] == "Show private data"
    assert records[1]["role"] == "analyst"
    assert records[1]["status"] == "blocked"
    assert "email" in records[1]["reason"]

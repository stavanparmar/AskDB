"""Discover the database schema and execute bounded read-only queries."""

import sqlite3
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from askdb.safety import validate_select


def describe_schema(engine: Engine) -> str:
    """Return a compact schema description with columns and relationships."""
    inspector = inspect(engine)
    descriptions = []
    for table_name in sorted(inspector.get_table_names()):
        columns = inspector.get_columns(table_name)
        column_text = ", ".join(
            f"{column['name']} {column['type']}" for column in columns
        )
        descriptions.append(f"{table_name} ({column_text})")

        for foreign_key in inspector.get_foreign_keys(table_name):
            source = ", ".join(foreign_key["constrained_columns"])
            target = (
                f"{foreign_key['referred_table']}."
                f"{', '.join(foreign_key['referred_columns'])}"
            )
            descriptions.append(f"  Foreign key: {table_name}.{source} -> {target}")

    return "\n".join(descriptions)


def _create_readonly_engine(database_path: Path) -> Engine:
    """Create an SQLAlchemy engine backed by a read-only SQLite connection."""
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"

    def connect_readonly() -> sqlite3.Connection:
        connection = sqlite3.connect(database_uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
        return connection

    return create_engine(
        "sqlite://",
        creator=connect_readonly,
        poolclass=NullPool,
    )


def run_readonly_query(database_path: Path, sql: str) -> pd.DataFrame:
    """Run a validated SELECT query and return at most the configured row cap."""
    safe_sql = validate_select(sql)
    engine = _create_readonly_engine(database_path)
    try:
        with engine.connect() as connection:
            return pd.read_sql_query(text(safe_sql), connection)
    finally:
        engine.dispose()

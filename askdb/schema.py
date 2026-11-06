"""Describe role-visible database schemas and run read-only queries."""

import sqlite3
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from askdb.permissions import RolePolicy


def describe_schema(engine: Engine, policy: RolePolicy) -> str:
    """Return only columns and relationships visible to the selected role."""
    inspector = inspect(engine)
    descriptions = []
    available_tables = {
        table.casefold(): table for table in inspector.get_table_names()
    }
    for policy_table in sorted(policy.tables):
        table_name = available_tables.get(policy_table.casefold())
        if table_name is None:
            raise ValueError(f"Policy table {policy_table!r} is missing from the database.")

        columns = inspector.get_columns(table_name)
        available_columns = {
            column["name"].casefold(): column for column in columns
        }
        requested_columns = policy.tables[policy_table]
        missing_columns = [
            column
            for column in requested_columns
            if column.casefold() not in available_columns
        ]
        if missing_columns:
            raise ValueError(
                f"Policy columns {', '.join(missing_columns)} are missing "
                f"from table {policy_table!r}."
            )
        column_text = ", ".join(
            f"{available_columns[column.casefold()]['name']} "
            f"{available_columns[column.casefold()]['type']}"
            for column in requested_columns
        )
        descriptions.append(f"{policy_table} ({column_text})")

        for foreign_key in inspector.get_foreign_keys(table_name):
            target_table = foreign_key["referred_table"]
            source_columns = foreign_key["constrained_columns"]
            target_columns = foreign_key["referred_columns"]
            target_policy = policy.tables.get(target_table)
            if (
                target_policy is not None
                and all(column in requested_columns for column in source_columns)
                and all(column in target_policy for column in target_columns)
            ):
                source = ", ".join(source_columns)
                target = f"{target_table}.{', '.join(target_columns)}"
                descriptions.append(
                    f"  Foreign key: {policy_table}.{source} -> {target}"
                )

    return "\n".join(descriptions)


def create_readonly_engine(database_path: Path) -> Engine:
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


def run_readonly_query(
    database: Path | Engine,
    sql: str,
    timeout_seconds: float = 30.0,
) -> pd.DataFrame:
    """Run bounded, pre-authorized SQL through a read-only connection."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero.")
    owns_engine = isinstance(database, Path)
    engine = create_readonly_engine(database) if owns_engine else database
    try:
        with engine.connect() as connection:
            progress_handler = None
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only = ON")
                deadline = time.monotonic() + timeout_seconds
                progress_handler = connection.connection.driver_connection
                progress_handler.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline),
                    1000,
                )
            elif engine.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                timeout_ms = max(1, int(timeout_seconds * 1000))
                connection.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": f"{timeout_ms}ms"},
                )
            try:
                return pd.read_sql_query(text(sql), connection)
            finally:
                if progress_handler is not None:
                    progress_handler.set_progress_handler(None, 0)
    finally:
        if owns_engine:
            engine.dispose()

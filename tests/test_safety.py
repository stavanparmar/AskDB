"""Tests for SQL validation and read-only query constraints."""

import pytest

from askdb.safety import SQLSafetyError, validate_select


def test_validate_select_adds_outer_row_limit() -> None:
    """Valid SELECT queries are wrapped with a bounded result limit."""
    safe_sql = validate_select("SELECT name FROM customers", max_rows=25)

    assert safe_sql == (
        "SELECT * FROM (SELECT name FROM customers) "
        "AS askdb_limited LIMIT 25"
    )


def test_validate_select_allows_one_trailing_semicolon() -> None:
    """A single trailing terminator is accepted and removed."""
    safe_sql = validate_select("SELECT 1;", max_rows=10)

    assert "SELECT 1;" not in safe_sql
    assert safe_sql.endswith("LIMIT 10")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customers (name) VALUES ('Mallory')",
        "SELECT * FROM customers; DROP TABLE customers",
        "SELECT * FROM customers WHERE name = 'x'; DELETE FROM customers",
        "SELECT * FROM customers -- comment\n",
        "SELECT * FROM customers /* comment */",
        "UPDATE customers SET name = 'Mallory'",
        "DROP TABLE customers",
    ],
)
def test_validate_select_rejects_unsafe_sql(sql: str) -> None:
    """Write operations, comments, and multiple statements are rejected."""
    with pytest.raises(SQLSafetyError):
        validate_select(sql)


def test_validate_select_does_not_treat_string_contents_as_sql() -> None:
    """SQL keywords and semicolons inside string literals are harmless."""
    safe_sql = validate_select(
        "SELECT name FROM customers WHERE note = 'DROP; TABLE'",
    )

    assert "'DROP; TABLE'" in safe_sql

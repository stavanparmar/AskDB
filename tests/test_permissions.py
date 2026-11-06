"""Tests for role-based SQL permissions."""

import pytest

from askdb.permissions import (
    SQLPermissionError,
    load_policy,
    validate,
)


@pytest.fixture(scope="module")
def policies():
    """Load the project role policies once for this test module."""
    return load_policy().roles


def test_validate_allows_aliased_join_and_order_by_alias(policies) -> None:
    """Joins resolve aliases and allow projected aliases in ORDER BY."""
    sql = (
        "SELECT c.city, SUM(o.total) AS revenue "
        "FROM customers AS c JOIN orders AS o ON c.id = o.customer_id "
        "GROUP BY c.city ORDER BY revenue DESC"
    )

    authorized = validate(sql, policies["analyst"])

    assert '"c"."city"' in authorized
    assert '"o"."total"' in authorized
    assert 'ORDER BY "revenue" DESC' in authorized
    assert authorized.endswith("LIMIT 100")


def test_validate_expands_select_star_to_allowed_columns(policies) -> None:
    """Star expansion omits columns unavailable to the selected role."""
    analyst_sql = validate("SELECT * FROM customers", policies["analyst"])
    manager_sql = validate("SELECT * FROM customers", policies["manager"])

    assert '"customers"."email"' not in analyst_sql
    assert '"customers"."email"' in manager_sql


def test_validate_rejects_unknown_table(policies) -> None:
    """A table absent from the policy is named in the error."""
    with pytest.raises(SQLPermissionError, match="Table 'secrets' is not allowed"):
        validate("SELECT id FROM secrets", policies["analyst"])


def test_validate_rejects_unknown_column(policies) -> None:
    """Unknown column references are rejected with their name."""
    with pytest.raises(SQLPermissionError, match="Column 'secret'"):
        validate("SELECT secret FROM customers", policies["analyst"])


def test_validate_rejects_column_outside_role_policy(policies) -> None:
    """Analysts cannot access customer email while managers can."""
    with pytest.raises(SQLPermissionError, match="email.*not allowed"):
        validate("SELECT c.email FROM customers AS c", policies["analyst"])

    assert '"c"."email"' in validate(
        "SELECT c.email FROM customers AS c",
        policies["manager"],
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM customers; SELECT id FROM orders",
        "SELECT id FROM customers; DELETE FROM customers",
    ],
)
def test_validate_rejects_multiple_statements(sql: str, policies) -> None:
    """Only one SQL statement is accepted."""
    with pytest.raises(SQLPermissionError, match="one SQL statement"):
        validate(sql, policies["analyst"])


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "DROP TABLE customers",
    ],
)
def test_validate_rejects_write_statements(sql: str, policies) -> None:
    """Write and DDL statements are rejected."""
    with pytest.raises(SQLPermissionError, match="Only SELECT"):
        validate(sql, policies["manager"])


def test_validate_allows_subquery_with_permitted_table(policies) -> None:
    """A SELECT subquery is allowed when all its tables are permitted."""
    authorized = validate(
        "SELECT id FROM customers WHERE id IN "
        "(SELECT customer_id FROM orders)",
        policies["analyst"],
    )

    assert "SELECT" in authorized
    assert '"orders"' in authorized


def test_validate_allows_nested_order_by_output_alias(policies) -> None:
    """Aliases projected by a nested SELECT are valid in its ORDER BY."""
    authorized = validate(
        "SELECT id FROM customers WHERE id IN "
        "(SELECT customer_id AS cid FROM orders ORDER BY cid)",
        policies["analyst"],
    )

    assert 'ORDER BY "cid"' in authorized


def test_validate_rejects_blocked_table_in_subquery(policies) -> None:
    """Tables inside subqueries are checked against the selected role."""
    with pytest.raises(
        SQLPermissionError,
        match="Table 'private_data' is not allowed",
    ):
        validate(
            "SELECT id FROM customers WHERE id IN "
            "(SELECT id FROM private_data)",
            policies["analyst"],
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM customers -- hidden suffix",
        "SELECT id FROM customers /* hidden suffix */",
    ],
)
def test_validate_rejects_comment_tricks(sql: str, policies) -> None:
    """SQL comments cannot conceal or alter generated query text."""
    with pytest.raises(SQLPermissionError, match="comments are not allowed"):
        validate(sql, policies["analyst"])


def test_validate_allows_comment_markers_inside_string_values(policies) -> None:
    """Comment-like text within a SQL string remains ordinary data."""
    authorized = validate("SELECT '/* not a comment */' AS note", policies["analyst"])

    assert "not a comment" in authorized


def test_validate_caps_existing_limit(policies) -> None:
    """A caller-supplied limit cannot exceed the configured row cap."""
    authorized = validate(
        "SELECT id FROM customers LIMIT 1000",
        policies["analyst"],
        limit=25,
    )

    assert authorized.endswith("LIMIT 25")


def test_validate_caps_negative_limit(policies) -> None:
    """SQLite's unlimited negative limit is replaced with the configured cap."""
    authorized = validate(
        "SELECT id FROM customers LIMIT -1",
        policies["analyst"],
        limit=25,
    )

    assert authorized.endswith("LIMIT 25")


def test_roles_receive_different_customer_columns(policies) -> None:
    """The same star query produces role-specific SQL projections."""
    analyst_sql = validate("SELECT * FROM customers", policies["analyst"])
    manager_sql = validate("SELECT * FROM customers", policies["manager"])

    assert analyst_sql != manager_sql
    assert "email" not in analyst_sql
    assert "email" in manager_sql

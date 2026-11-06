"""Load role policies and authorize generated read-only SQL."""

from pathlib import Path
import re
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify


POLICY_PATH = Path(__file__).resolve().parents[1] / "policies.yaml"


class PolicyConfigurationError(ValueError):
    """Raised when the role policy cannot be loaded or validated."""


class SQLPermissionError(ValueError):
    """Raised when a query violates the selected role's permissions."""


class RolePolicy(BaseModel):
    """Tables and columns available to one role."""

    model_config = ConfigDict(extra="forbid")

    tables: dict[str, list[str]] = Field(min_length=1)

    @field_validator("tables")
    @classmethod
    def validate_table_columns(cls, tables: dict[str, list[str]]) -> dict[str, list[str]]:
        """Require each configured table to have a non-empty column list."""
        for table, columns in tables.items():
            if not table or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                raise ValueError(f"Invalid table name in policy: {table!r}.")
            if not columns:
                raise ValueError(f"Table {table!r} must allow at least one column.")
            if len({column.casefold() for column in columns}) != len(columns):
                raise ValueError(f"Table {table!r} contains duplicate columns.")
            if any(
                not column
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column)
                for column in columns
            ):
                raise ValueError(f"Table {table!r} contains an invalid column name.")
        return tables


class PolicyDocument(BaseModel):
    """Validated role policies loaded from the project YAML file."""

    model_config = ConfigDict(extra="forbid")

    roles: dict[str, RolePolicy] = Field(min_length=1)

    _role_name_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"[A-Za-z_][A-Za-z0-9_]*"
    )

    @field_validator("roles")
    @classmethod
    def validate_role_names(cls, roles: dict[str, RolePolicy]) -> dict[str, RolePolicy]:
        """Require simple role identifiers for predictable UI selection."""
        if any(not cls._role_name_pattern.fullmatch(role) for role in roles):
            raise ValueError("Policy role names must be valid identifiers.")
        return roles


def load_policy(path: Path = POLICY_PATH) -> PolicyDocument:
    """Load a YAML policy file and validate its role/table/column structure."""
    try:
        with path.open(encoding="utf-8") as policy_file:
            data = yaml.safe_load(policy_file)
        return PolicyDocument.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise PolicyConfigurationError(
            f"Could not load a valid permissions policy from {path}: {error}"
        ) from error


def _reject_comments(sql: str) -> None:
    """Reject SQL comments without mistaking quoted text for a comment."""
    quote_pairs = {"'": "'", '"': '"', "`": "`", "[": "]"}
    index = 0
    while index < len(sql):
        character = sql[index]
        if sql.startswith("--", index) or sql.startswith("/*", index):
            raise SQLPermissionError("SQL comments are not allowed.")
        if character not in quote_pairs:
            index += 1
            continue

        closing_quote = quote_pairs[character]
        index += 1
        while index < len(sql):
            if sql[index] == closing_quote:
                if index + 1 < len(sql) and sql[index + 1] == closing_quote:
                    index += 2
                    continue
                index += 1
                break
            index += 1
    return


def _validate_tables(
    query: exp.Select,
    policy: RolePolicy,
) -> tuple[dict[str, str], list[str]]:
    """Check all base tables and return table aliases and referenced tables."""
    allowed_tables = {table.casefold(): table for table in policy.tables}
    cte_names = {
        cte.alias_or_name.casefold()
        for cte in query.find_all(exp.CTE)
        if cte.alias_or_name
    }
    aliases: dict[str, str] = {}
    referenced_tables: list[str] = []

    for table in query.find_all(exp.Table):
        table_name = table.name.casefold()
        if table_name in cte_names:
            continue
        qualified_name = ".".join(
            part for part in (table.catalog, table.db, table.name) if part
        )
        if table.catalog or (table.db and table.db.casefold() != "main"):
            raise SQLPermissionError(f"Table {qualified_name!r} is not allowed.")
        allowed_name = allowed_tables.get(table_name)
        if allowed_name is None:
            raise SQLPermissionError(f"Table {table.name!r} is not allowed.")
        referenced_tables.append(allowed_name)
        aliases[table.alias_or_name.casefold()] = allowed_name
        aliases[table.name.casefold()] = allowed_name

    if not referenced_tables and not cte_names and not query.find_all(exp.Table):
        return aliases, referenced_tables
    return aliases, referenced_tables


def _validate_qualified_columns(
    query: exp.Select,
    policy: RolePolicy,
    aliases: dict[str, str],
    referenced_tables: list[str],
) -> None:
    """Give role-specific errors for explicit columns outside the policy."""
    allowed_columns = {
        table.casefold(): {column.casefold() for column in columns}
        for table, columns in policy.tables.items()
    }
    output_aliases = {
        projection.alias.casefold()
        for select in (query, *query.find_all(exp.Select))
        for projection in select.expressions
        if isinstance(projection, exp.Alias)
    }
    available_columns = {
        column.casefold()
        for table in referenced_tables
        for column in policy.tables[table]
    }
    cte_names = {
        cte.alias_or_name.casefold()
        for cte in query.find_all(exp.CTE)
        if cte.alias_or_name
    }

    for column in query.find_all(exp.Column):
        if column.is_star:
            continue
        column_name = column.name.casefold()
        if column.table:
            source_name = column.table.casefold()
            source_table = aliases.get(source_name)
            if source_table is not None:
                if column_name not in allowed_columns[source_table.casefold()]:
                    raise SQLPermissionError(
                        f"Column {column.name!r} on table {source_table!r} "
                        "is not allowed for this role."
                    )
            elif source_name not in cte_names:
                raise SQLPermissionError(
                    f"Column {column.name!r} on table {column.table!r} "
                    "is not allowed for this role."
                )
            continue

        if (
            column_name in output_aliases
            and column.find_ancestor(exp.Order) is not None
        ):
            continue
        if column_name not in available_columns and column_name not in output_aliases:
            raise SQLPermissionError(
                f"Column {column.name!r} is not allowed for this role."
            )


def validate(sql: str, policy: RolePolicy, limit: int = 100) -> str:
    """Authorize one SELECT and return qualified SQL with a bounded row count."""
    if not sql or not sql.strip():
        raise SQLPermissionError("The SQL query is empty.")
    if limit < 1:
        raise ValueError("limit must be greater than zero.")
    _reject_comments(sql)

    try:
        statements = parse(sql, read="sqlite")
    except SqlglotError as error:
        raise SQLPermissionError(f"Could not parse SQL: {error}") from error
    if len(statements) != 1 or statements[0] is None:
        raise SQLPermissionError("Only one SQL statement is allowed.")

    query = statements[0]
    if not isinstance(query, exp.Select):
        raise SQLPermissionError("Only SELECT queries are allowed.")
    if any(
        isinstance(node, (exp.Delete, exp.Drop, exp.Insert, exp.Update, exp.Create))
        for node in query.walk()
    ):
        raise SQLPermissionError("Only SELECT queries are allowed.")

    aliases, referenced_tables = _validate_tables(query, policy)
    _validate_qualified_columns(query, policy, aliases, referenced_tables)

    schema = {
        table: {column: "TEXT" for column in columns}
        for table, columns in policy.tables.items()
    }
    try:
        qualified_query = qualify(
            query,
            dialect="sqlite",
            schema=schema,
            expand_stars=True,
            validate_qualify_columns=True,
        )
    except SqlglotError as error:
        raise SQLPermissionError(
            f"A referenced column is not allowed or could not be resolved: {error}"
        ) from error

    current_limit = qualified_query.args.get("limit")
    if current_limit is None:
        qualified_query.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
    else:
        limit_value = current_limit.expression
        if not isinstance(limit_value, exp.Literal) or not limit_value.is_int:
            qualified_query.set(
                "limit",
                exp.Limit(expression=exp.Literal.number(limit)),
            )
        else:
            requested_limit = int(limit_value.this)
            if requested_limit < 0 or requested_limit > limit:
                limit_value.set("this", str(limit))

    return qualified_query.sql(dialect="sqlite")

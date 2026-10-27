"""Validate generated SQL and enforce read-only result limits."""

import re


MAX_ROWS = 200
_BLOCKED_KEYWORDS = {
    "ALTER",
    "ATTACH",
    "BEGIN",
    "COMMIT",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "INSERT",
    "PRAGMA",
    "REINDEX",
    "REPLACE",
    "ROLLBACK",
    "SAVEPOINT",
    "TRIGGER",
    "UPDATE",
    "VACUUM",
}


class SQLSafetyError(ValueError):
    """Raised when SQL does not meet the application's read-only policy."""


def _mask_quoted_sections(sql: str) -> str:
    """Mask quoted text while rejecting comments and malformed quotes."""
    masked = list(sql)
    index = 0
    quote_pairs = {"'": "'", '"': '"', "`": "`", "[": "]"}

    while index < len(sql):
        char = sql[index]
        if sql.startswith("--", index) or sql.startswith("/*", index):
            raise SQLSafetyError("SQL comments are not allowed.")
        if char not in quote_pairs:
            index += 1
            continue

        closing_quote = quote_pairs[char]
        masked[index] = " "
        index += 1
        while index < len(sql):
            masked[index] = " "
            if sql[index] == closing_quote:
                if index + 1 < len(sql) and sql[index + 1] == closing_quote:
                    masked[index + 1] = " "
                    index += 2
                    continue
                index += 1
                break
            index += 1
        else:
            raise SQLSafetyError("SQL contains an unterminated quoted value.")

    return "".join(masked)


def validate_select(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Validate a single SELECT statement and wrap it with a row limit."""
    if not isinstance(sql, str) or not sql.strip():
        raise SQLSafetyError("The generated SQL query is empty.")
    if max_rows < 1:
        raise ValueError("max_rows must be greater than zero.")

    visible_sql = _mask_quoted_sections(sql)
    semicolons = [index for index, char in enumerate(visible_sql) if char == ";"]
    query_end = len(sql.rstrip())
    if semicolons:
        if len(semicolons) != 1 or semicolons[0] != query_end - 1:
            raise SQLSafetyError("Only one SQL statement is allowed.")
        sql = sql[: semicolons[0]].rstrip()
        visible_sql = visible_sql[: semicolons[0]].rstrip()

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", visible_sql.upper())
    if not tokens or tokens[0] != "SELECT":
        raise SQLSafetyError("Only SELECT queries are allowed.")
    blocked = _BLOCKED_KEYWORDS.intersection(tokens)
    if blocked:
        raise SQLSafetyError(
            f"Disallowed SQL operation: {', '.join(sorted(blocked))}."
        )

    return f"SELECT * FROM ({sql.strip()}) AS askdb_limited LIMIT {max_rows}"

"""Streamlit chat interface for asking questions about the sample database."""

from io import StringIO
from pathlib import Path
from typing import TypedDict

import pandas as pd
import streamlit as st
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from askdb.chain import (
    LLMConfigurationError,
    LLMRequestError,
    generate_sql,
    summarize_result,
)
from askdb.safety import SQLSafetyError
from askdb.schema import describe_schema, run_readonly_query
from askdb.seed import DATABASE_PATH, create_database_engine, seed_database


class ChatRecord(TypedDict):
    """Stored question and response data for one chat turn."""

    question: str
    sql: str
    summary: str
    result_json: str
    error: str


class QueryExecutionError(RuntimeError):
    """Raised when a generated query still fails after its retry."""


@st.cache_resource
def _database_engine() -> Engine:
    """Create and seed the local sample database once per app process."""
    engine = create_database_engine()
    seed_database(engine)
    return engine


def _execute_with_retry(question: str, schema: str) -> tuple[str, pd.DataFrame]:
    """Generate and run a query, sending one execution error back to the model."""
    previous_sql = ""
    error_message = ""
    for attempt in range(2):
        sql = generate_sql(
            question,
            schema,
            previous_sql=previous_sql,
            error_message=error_message,
        )
        try:
            results = run_readonly_query(DATABASE_PATH, sql)
            return sql, results
        except (SQLAlchemyError, SQLSafetyError) as error:
            if attempt == 1:
                raise QueryExecutionError(str(error)) from error
            previous_sql = sql
            error_message = str(error)[:500]
    raise QueryExecutionError("The query could not be completed.")


def _chart_columns(
    results: pd.DataFrame,
) -> tuple[str, str, str] | None:
    """Choose a chart type and its x/y columns when the result supports one."""
    numeric_columns = [
        column
        for column in results.select_dtypes(include="number").columns
        if column.lower() != "id" and not column.lower().endswith("_id")
    ]
    if not numeric_columns:
        return None

    date_columns = [
        column
        for column in results.columns
        if "date" in column.lower() or "time" in column.lower()
    ]
    for column in date_columns:
        converted = pd.to_datetime(results[column], errors="coerce")
        if converted.notna().any():
            return "line", column, numeric_columns[0]

    category_columns = [
        column
        for column in results.select_dtypes(include=["object", "category"]).columns
        if column.lower() != "id"
        and not column.lower().endswith("_id")
        and 1 < results[column].nunique(dropna=True) <= 50
    ]
    if category_columns:
        return "bar", category_columns[0], numeric_columns[0]
    return None


def _render_chart(results: pd.DataFrame) -> None:
    """Render a bar or line chart when the result has suitable columns."""
    selection = _chart_columns(results)
    if selection is None:
        return

    chart_type, x_column, y_column = selection
    chart_data = results[[x_column, y_column]].copy()
    if chart_type == "line":
        chart_data[x_column] = pd.to_datetime(
            chart_data[x_column],
            errors="coerce",
        )
        chart_data = chart_data.dropna(subset=[x_column])
        chart_data = (
            chart_data.groupby(x_column, as_index=False)[y_column]
            .sum()
            .sort_values(x_column)
        )
        if not chart_data.empty:
            st.line_chart(chart_data.set_index(x_column)[[y_column]])
        return

    chart_data = (
        chart_data.groupby(x_column, as_index=False)[y_column]
        .sum()
        .sort_values(y_column, ascending=False)
        .head(30)
    )
    st.bar_chart(chart_data, x=x_column, y=y_column)


def _render_record(record: ChatRecord) -> None:
    """Render a stored assistant response and its table/chart."""
    with st.chat_message("user"):
        st.markdown(record["question"])
    with st.chat_message("assistant"):
        if record["error"]:
            st.error(record["error"])
            return
        st.code(record["sql"], language="sql")
        if record["summary"]:
            st.write(record["summary"])
        else:
            st.warning("The query succeeded, but its summary could not be generated.")
        results = pd.read_json(StringIO(record["result_json"]), orient="split")
        st.dataframe(results, use_container_width=True)
        _render_chart(results)


def _answer_question(question: str, schema: str) -> ChatRecord:
    """Run a question through SQL generation, execution, and summarization."""
    sql, results = _execute_with_retry(question, schema)
    try:
        summary = summarize_result(question, sql, results)
    except (LLMConfigurationError, LLMRequestError, ValueError):
        summary = ""

    return {
        "question": question,
        "sql": sql,
        "summary": summary,
        "result_json": results.to_json(orient="split", date_format="iso"),
        "error": "",
    }


def main() -> None:
    """Render AskDB and process chat questions."""
    st.set_page_config(page_title="AskDB", page_icon="💬", layout="wide")
    st.title("AskDB")
    st.write("Ask questions in plain English about the sample sales database.")

    try:
        schema = describe_schema(_database_engine())
    except SQLAlchemyError:
        st.error("The sample database could not be initialized.")
        return

    st.caption("Sample data: customers, products, and orders (500 records total).")
    if "askdb_history" not in st.session_state:
        st.session_state["askdb_history"] = []

    history: list[ChatRecord] = st.session_state["askdb_history"]
    for record in history:
        _render_record(record)

    question = st.chat_input("Ask a question about customers, products, or orders")
    if not question:
        return

    try:
        with st.spinner("Generating and safely running your query..."):
            record = _answer_question(question, schema)
    except (LLMConfigurationError, LLMRequestError) as error:
        record = {
            "question": question,
            "sql": "",
            "summary": "",
            "result_json": "",
            "error": str(error),
        }
    except QueryExecutionError:
        record = {
            "question": question,
            "sql": "",
            "summary": "",
            "result_json": "",
            "error": "The query failed after one retry. Try rephrasing your question.",
        }

    history.append(record)
    _render_record(record)


if __name__ == "__main__":
    main()

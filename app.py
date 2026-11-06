"""Role-aware Streamlit chat interface for asking questions about AskDB."""

import os
from io import StringIO
from typing import NotRequired, TypedDict

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from askdb.audit import log_question
from askdb.chain import (
    LLMConfigurationError,
    LLMRequestError,
    generate_sql,
    summarize_result,
)
from askdb.permissions import (
    PolicyConfigurationError,
    RolePolicy,
    SQLPermissionError,
    load_policy,
    validate,
)
from askdb.schema import (
    create_readonly_engine,
    describe_schema,
    run_readonly_query,
)
from askdb.seed import DATABASE_PATH, create_database_engine, seed_database


class ChatRecord(TypedDict):
    """Stored question and response data for one chat turn."""

    question: str
    sql: str
    summary: str
    result_json: str
    error: str
    role: NotRequired[str]
    error_title: NotRequired[str]


@st.cache_resource
def _database_engine(role: str) -> Engine:
    """Return a role-specific PostgreSQL or read-only sample SQLite engine."""
    database_url = (
        os.getenv(f"ASKDB_{role.upper()}_DATABASE_URL")
        or os.getenv("ASKDB_DATABASE_URL")
    )
    if database_url:
        engine = create_engine(database_url, pool_pre_ping=True)
        if engine.dialect.name != "postgresql":
            engine.dispose()
            raise ValueError("ASKDB_DATABASE_URL must use PostgreSQL.")
        return engine

    writable_engine = create_database_engine()
    try:
        seed_database(writable_engine)
    finally:
        writable_engine.dispose()
    return create_readonly_engine(DATABASE_PATH)


def _chart_columns(results: pd.DataFrame) -> tuple[str, str, str] | None:
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
    """Render a Plotly bar or line chart for suitable query results."""
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
            figure = px.line(chart_data, x=x_column, y=y_column, markers=True)
            st.plotly_chart(figure, use_container_width=True, theme="streamlit")
        return

    chart_data = (
        chart_data.groupby(x_column, as_index=False)[y_column]
        .sum()
        .sort_values(y_column, ascending=False)
        .head(30)
    )
    figure = px.bar(chart_data, x=x_column, y=y_column)
    st.plotly_chart(figure, use_container_width=True, theme="streamlit")


def _render_record(record: ChatRecord) -> None:
    """Render a stored assistant response and its table/chart."""
    with st.chat_message("user"):
        st.markdown(record["question"])
    with st.chat_message("assistant"):
        role = record.get("role", "analyst")
        st.caption(f"Role: {role.title()}")
        if record["error"]:
            with st.container(border=True):
                st.error(record.get("error_title", "Request failed"))
                st.write(record["error"])
            return

        if record["summary"]:
            st.write(record["summary"])
        else:
            st.warning("The query succeeded, but its summary could not be generated.")
        results = pd.read_json(StringIO(record["result_json"]), orient="split")
        st.dataframe(
            results.style.format(precision=2),
            use_container_width=True,
            hide_index=True,
        )
        _render_chart(results)
        with st.expander("SQL"):
            st.code(record["sql"], language="sql")


def _error_record(
    question: str,
    role: str,
    error: str,
    title: str,
) -> ChatRecord:
    """Build the consistent chat response for a denied or failed request."""
    return {
        "question": question,
        "sql": "",
        "summary": "",
        "result_json": "",
        "error": error,
        "role": role,
        "error_title": title,
    }


def _answer_question(
    question: str,
    role: str,
    policy: RolePolicy,
) -> ChatRecord:
    """Generate, authorize, audit, and run a question for the selected role."""
    sql = ""
    reason = ""
    error_title = "Request failed"
    audit_written = False
    progress = st.status("Reading schema", expanded=True)
    try:
        with progress:
            engine = _database_engine(role)
            schema = describe_schema(engine, policy)
            progress.write("Using only tables and columns allowed for this role.")

            progress.update(label="Generating SQL", state="running")
            generated_sql = generate_sql(question, schema)
            sql = generated_sql
            progress.write("SQL generated.")

            progress.update(label="Checking permissions", state="running")
            safe_sql = validate(generated_sql, policy, limit=100)
            sql = safe_sql
            log_question(question, sql, role, "allowed")
            audit_written = True

            progress.update(label="Running query", state="running")
            results = run_readonly_query(engine, safe_sql)
            try:
                summary = summarize_result(question, safe_sql, results)
            except (LLMConfigurationError, LLMRequestError, ValueError):
                summary = ""

            progress.update(label="Query complete", state="complete")
            return {
                "question": question,
                "sql": safe_sql,
                "summary": summary,
                "result_json": results.to_json(orient="split", date_format="iso"),
                "error": "",
                "role": role,
            }
    except SQLPermissionError as error:
        reason = str(error)
        error_title = "Query blocked"
        progress.update(label="Query blocked by permissions", state="error")
    except (LLMConfigurationError, LLMRequestError) as error:
        reason = str(error)
        error_title = "Could not generate query"
        progress.update(label="Could not generate the query", state="error")
    except (SQLAlchemyError, ValueError) as error:
        reason = str(error)
        error_title = "Query failed"
        progress.update(label="Could not run the query", state="error")
    finally:
        if not audit_written:
            log_question(question, sql, role, "blocked", reason)

    return _error_record(question, role, reason, error_title)


def _render_access_panel(policy: RolePolicy) -> None:
    """Show the selected role's permitted tables and columns in the sidebar."""
    st.sidebar.subheader("Your access")
    for table, columns in policy.tables.items():
        st.sidebar.markdown(f"**{table}**")
        st.sidebar.caption(", ".join(columns))


def main() -> None:
    """Render AskDB and process role-aware chat questions."""
    st.set_page_config(page_title="AskDB", page_icon="💬", layout="wide")
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] .block-container {
            padding-top: 1.8rem;
            padding-bottom: 2rem;
            max-width: 1200px;
        }
        [data-testid="stChatMessage"],
        [data-testid="stDataFrame"],
        [data-testid="stAlert"] {
            border-radius: 14px;
        }
        [data-testid="stChatMessage"] {
            padding: 0.9rem 1rem;
            margin-bottom: 0.6rem;
        }
        [data-testid="stSidebar"] {
            border-right: 1px solid var(--secondary-background-color);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("AskDB")
    st.write("Ask questions in plain English about the sample sales database.")

    try:
        policy_document = load_policy()
    except PolicyConfigurationError as error:
        st.error(str(error))
        st.stop()

    role_names = list(policy_document.roles)
    role = st.sidebar.selectbox(
        "Choose a role",
        role_names,
        format_func=str.title,
    )
    policy = policy_document.roles[role]
    _render_access_panel(policy)
    st.sidebar.info(
        "The role selector is for this local demo. Production deployments must "
        "derive the role from authenticated identity."
    )

    if st.session_state.get("askdb_history_role") not in (None, role):
        st.session_state["askdb_history"] = []
    st.session_state["askdb_history_role"] = role
    if "askdb_history" not in st.session_state:
        st.session_state["askdb_history"] = []

    history: list[ChatRecord] = st.session_state["askdb_history"]
    for record in history:
        _render_record(record)

    question = st.chat_input("Ask a question about customers, products, or orders")
    if not question:
        return

    record = _answer_question(question, role, policy)
    history.append(record)
    _render_record(record)


if __name__ == "__main__":
    main()

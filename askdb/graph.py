"""LangGraph workflow for generating, checking, and running AskDB queries."""

from typing import Any, Callable, Literal, TypedDict

import pandas as pd
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from askdb.audit import log_question
from askdb.chain import (
    LLMConfigurationError,
    LLMRequestError,
    generate_sql,
    summarize_result,
)
from askdb.permissions import RolePolicy, SQLPermissionError, validate
from askdb.schema import describe_schema, run_readonly_query


ChartChoice = tuple[str, str, str] | None
ErrorKind = Literal[
    "",
    "permission",
    "execution",
    "llm",
    "schema",
    "summary",
]


class AskDBState(TypedDict):
    """Data passed between nodes in a single AskDB workflow run."""

    question: str
    role: str
    schema: str
    sql: str
    error: str
    error_kind: ErrorKind
    rows: list[dict[str, Any]]
    columns: list[str]
    summary: str
    attempts: int
    chart: ChartChoice


def initial_state(question: str, role: str) -> AskDBState:
    """Create the empty state for one question and selected role."""
    return {
        "question": question,
        "role": role,
        "schema": "",
        "sql": "",
        "error": "",
        "error_kind": "",
        "rows": [],
        "columns": [],
        "summary": "",
        "attempts": 0,
        "chart": None,
    }


def _choose_chart(results: pd.DataFrame) -> ChartChoice:
    """Choose a chart type and columns when query results support one."""
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
        if pd.to_datetime(results[column], errors="coerce").notna().any():
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


def build_graph(
    engine: Engine,
    policies: dict[str, RolePolicy],
    *,
    sql_generator: Callable[[str, str, str, str], str] = generate_sql,
    result_summarizer: Callable[[str, str, pd.DataFrame], str] = summarize_result,
    schema_loader: Callable[[Engine, RolePolicy], str] = describe_schema,
    query_runner: Callable[[Engine, str, float], pd.DataFrame] = run_readonly_query,
) -> CompiledStateGraph:
    """Build the role-aware query workflow with injectable dependencies."""

    def get_schema(state: AskDBState) -> dict[str, Any]:
        try:
            policy = policies[state["role"]]
            return {"schema": schema_loader(engine, policy)}
        except (KeyError, SQLAlchemyError, ValueError) as error:
            return {
                "error": str(error),
                "error_kind": "schema",
            }

    def generate(state: AskDBState) -> dict[str, Any]:
        attempt = state["attempts"] + 1
        try:
            sql = sql_generator(
                state["question"],
                state["schema"],
                state["sql"] if state["attempts"] else "",
                state["error"] if state["attempts"] else "",
            )
        except (LLMConfigurationError, LLMRequestError, ValueError) as error:
            return {
                "attempts": attempt,
                "error": str(error),
                "error_kind": "llm",
            }
        return {
            "sql": sql,
            "attempts": attempt,
            "error": "",
            "error_kind": "",
        }

    def check_permissions(state: AskDBState) -> dict[str, Any]:
        if state["error_kind"]:
            return {}
        try:
            safe_sql = validate(state["sql"], policies[state["role"]], limit=100)
        except SQLPermissionError as error:
            return {"error": str(error), "error_kind": "permission"}
        return {"sql": safe_sql}

    def execute(state: AskDBState) -> dict[str, Any]:
        try:
            results = query_runner(engine, state["sql"], timeout_seconds=30)
        except (SQLAlchemyError, TimeoutError, ValueError) as error:
            return {"error": str(error), "error_kind": "execution"}
        return {
            "rows": results.to_dict(orient="records"),
            "columns": list(results.columns),
            "error": "",
            "error_kind": "",
        }

    def summarize(state: AskDBState) -> dict[str, Any]:
        results = pd.DataFrame(state["rows"], columns=state["columns"])
        try:
            answer = result_summarizer(state["question"], state["sql"], results)
        except (LLMConfigurationError, LLMRequestError, ValueError) as error:
            return {"error": str(error), "error_kind": "summary"}
        log_question(
            state["question"],
            state["sql"],
            state["role"],
            "allowed",
            attempts=state["attempts"],
        )
        return {
            "summary": answer,
            "chart": _choose_chart(results),
            "error": "",
            "error_kind": "",
        }

    def refuse(state: AskDBState) -> dict[str, Any]:
        message = (
            "I couldn’t run that query because it requests data outside your "
            "role’s permissions."
            if state["error_kind"] == "permission"
            else "I couldn’t complete that query. Please try rephrasing it."
        )
        log_question(
            state["question"],
            state["sql"],
            state["role"],
            "blocked",
            state["error"],
            attempts=state["attempts"],
        )
        return {"summary": message}

    def after_generation(state: AskDBState) -> str:
        return "refuse" if state["error_kind"] else "check_permissions"

    def after_permissions(state: AskDBState) -> str:
        return "refuse" if state["error_kind"] == "permission" else "execute"

    def after_execution(state: AskDBState) -> str:
        if not state["error_kind"]:
            return "summarize"
        return "generate_sql" if state["attempts"] < 2 else "refuse"

    def after_summary(state: AskDBState) -> str:
        return "refuse" if state["error_kind"] else END

    workflow = StateGraph(AskDBState)
    workflow.add_node("get_schema", get_schema)
    workflow.add_node("generate_sql", generate)
    workflow.add_node("check_permissions", check_permissions)
    workflow.add_node("execute", execute)
    workflow.add_node("summarize", summarize)
    workflow.add_node("refuse", refuse)

    workflow.add_edge(START, "get_schema")
    workflow.add_conditional_edges(
        "get_schema",
        lambda state: "refuse" if state["error_kind"] else "generate_sql",
    )
    workflow.add_conditional_edges("generate_sql", after_generation)
    workflow.add_conditional_edges("check_permissions", after_permissions)
    workflow.add_conditional_edges("execute", after_execution)
    workflow.add_conditional_edges("summarize", after_summary)
    workflow.add_edge("refuse", END)
    return workflow.compile()

"""Tests for the AskDB query workflow and its retry routing."""

from unittest.mock import Mock

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

import askdb.graph as graph_module
from askdb.graph import build_graph, initial_state
from askdb.permissions import RolePolicy
from langgraph.graph.state import CompiledStateGraph


def _build_test_graph(
    monkeypatch: pytest.MonkeyPatch,
    sql_generator: Mock,
    query_runner: Mock,
) -> tuple[CompiledStateGraph, Mock]:
    """Build a workflow with isolated schema, LLM, database, and audit calls."""
    audit = Mock()
    monkeypatch.setattr(graph_module, "log_question", audit)
    graph = build_graph(
        create_engine("sqlite://"),
        {
            "analyst": RolePolicy(
                tables={"customers": ["id", "name", "city", "total"]}
            )
        },
        sql_generator=sql_generator,
        result_summarizer=Mock(return_value="The query returned two customers."),
        schema_loader=Mock(return_value="customers (id, name)"),
        query_runner=query_runner,
    )
    return graph, audit


def test_graph_happy_path_generates_executes_and_summarizes(monkeypatch) -> None:
    """A permitted query executes once and writes a successful audit record."""
    sql_generator = Mock(return_value="SELECT city, total FROM customers")
    query_runner = Mock(
        return_value=pd.DataFrame(
            {"city": ["A", "B"], "total": [4, 7]},
        )
    )
    graph, audit = _build_test_graph(monkeypatch, sql_generator, query_runner)

    state = graph.invoke(initial_state("Totals by city", "analyst"))

    assert state["summary"] == "The query returned two customers."
    assert state["attempts"] == 1
    assert state["chart"] == ("bar", "city", "total")
    query_runner.assert_called_once()
    audit.assert_called_once()
    assert audit.call_args.args[3] == "allowed"
    assert audit.call_args.kwargs["attempts"] == 1


def test_graph_refuses_permission_violation_without_retry(monkeypatch) -> None:
    """A disallowed column goes directly to refusal without database execution."""
    sql_generator = Mock(return_value="SELECT email FROM customers")
    query_runner = Mock()
    graph, audit = _build_test_graph(monkeypatch, sql_generator, query_runner)

    state = graph.invoke(initial_state("Show customer emails", "analyst"))

    assert state["error_kind"] == "permission"
    assert "couldn’t run" in state["summary"]
    assert state["attempts"] == 1
    query_runner.assert_not_called()
    sql_generator.assert_called_once()
    audit.assert_called_once()
    assert audit.call_args.args[3] == "blocked"
    assert audit.call_args.kwargs["attempts"] == 1


def test_graph_retries_execution_error_once_and_succeeds(monkeypatch) -> None:
    """An execution error is passed to the second generation attempt."""
    sql_generator = Mock(
        side_effect=[
            "SELECT id FROM customers",
            "SELECT name FROM customers",
        ]
    )
    query_runner = Mock(
        side_effect=[
            SQLAlchemyError("temporary database error"),
            pd.DataFrame({"name": ["Ada"]}),
        ]
    )
    graph, audit = _build_test_graph(monkeypatch, sql_generator, query_runner)

    state = graph.invoke(initial_state("List customers", "analyst"))

    assert state["attempts"] == 2
    assert state["error_kind"] == ""
    assert state["summary"] == "The query returned two customers."
    assert sql_generator.call_count == 2
    retry_args = sql_generator.call_args_list[1].args
    assert "customers" in retry_args[2]
    assert "temporary database error" in retry_args[3]
    assert query_runner.call_count == 2
    audit.assert_called_once()
    assert audit.call_args.args[3] == "allowed"
    assert audit.call_args.kwargs["attempts"] == 2


def test_graph_refuses_after_two_execution_errors(monkeypatch) -> None:
    """Two failed executions stop the workflow and produce one audit record."""
    sql_generator = Mock(
        side_effect=[
            "SELECT id FROM customers",
            "SELECT name FROM customers",
        ]
    )
    query_runner = Mock(
        side_effect=[
            SQLAlchemyError("first database error"),
            SQLAlchemyError("second database error"),
        ]
    )
    graph, audit = _build_test_graph(monkeypatch, sql_generator, query_runner)

    state = graph.invoke(initial_state("List customers", "analyst"))

    assert state["error_kind"] == "execution"
    assert state["attempts"] == 2
    assert "couldn’t complete" in state["summary"]
    assert sql_generator.call_count == 2
    assert query_runner.call_count == 2
    audit.assert_called_once()
    assert audit.call_args.args[3] == "blocked"
    assert audit.call_args.kwargs["attempts"] == 2

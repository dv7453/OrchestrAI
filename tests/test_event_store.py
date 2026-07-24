"""Tests for the EventStore."""
import os
import tempfile
import pytest

from langgraph_browser_agent.event_store import EventStore, ExecutionEvent


@pytest.fixture
def store(tmp_path):
    """Create a fresh EventStore backed by a temp file."""
    db = tmp_path / "test_events.db"
    return EventStore(db_path=db)


class TestEventStoreEmit:
    """Test event emission."""

    def test_emit_and_retrieve(self, store):
        event = ExecutionEvent(
            run_id="run-1",
            event_type="node_entered",
            step_number=0,
            node_name="prepare_context",
            payload={"foo": "bar"},
        )
        store.emit(event)

        events = store.get_run_events("run-1")
        assert len(events) == 1
        assert events[0]["event_type"] == "node_entered"
        assert events[0]["node_name"] == "prepare_context"

    def test_emit_multiple_events(self, store):
        for i in range(5):
            store.emit(
                ExecutionEvent(
                    run_id="run-2",
                    event_type=f"event_{i}",
                    step_number=i,
                )
            )

        events = store.get_run_events("run-2")
        assert len(events) == 5

    def test_events_ordered_by_timestamp(self, store):
        import time

        store.emit(ExecutionEvent(run_id="run-3", event_type="first", timestamp=100.0))
        store.emit(ExecutionEvent(run_id="run-3", event_type="second", timestamp=200.0))
        store.emit(ExecutionEvent(run_id="run-3", event_type="third", timestamp=150.0))

        events = store.get_run_events("run-3")
        assert [e["event_type"] for e in events] == ["first", "third", "second"]


class TestEventStoreRunSummary:
    """Test run summary generation."""

    def test_empty_run_summary(self, store):
        summary = store.get_run_summary("nonexistent")
        assert summary == {}

    def test_run_summary_with_events(self, store):
        store.emit(ExecutionEvent(run_id="run-4", event_type="run_started", timestamp=100.0))
        store.emit(
            ExecutionEvent(
                run_id="run-4",
                event_type="node_entered",
                node_name="prepare_context",
                timestamp=101.0,
            )
        )
        store.emit(
            ExecutionEvent(
                run_id="run-4",
                event_type="node_exited",
                node_name="prepare_context",
                timestamp=102.5,
            )
        )
        store.emit(
            ExecutionEvent(
                run_id="run-4",
                event_type="node_entered",
                node_name="finalize_step",
                timestamp=103.0,
            )
        )
        store.emit(
            ExecutionEvent(
                run_id="run-4",
                event_type="node_exited",
                node_name="finalize_step",
                timestamp=103.5,
            )
        )
        store.emit(ExecutionEvent(run_id="run-4", event_type="run_completed", timestamp=104.0))

        summary = store.get_run_summary("run-4")
        assert summary["total_events"] == 6
        assert summary["total_steps"] == 1
        assert "prepare_context" in summary["node_durations"]
        assert abs(summary["node_durations"]["prepare_context"] - 1.5) < 0.01


class TestEventStoreListRuns:
    """Test run listing."""

    def test_list_runs(self, store):
        store.emit(ExecutionEvent(run_id="run-a", event_type="start", timestamp=100.0))
        store.emit(ExecutionEvent(run_id="run-a", event_type="end", timestamp=200.0))
        store.emit(ExecutionEvent(run_id="run-b", event_type="start", timestamp=300.0))

        runs = store.list_runs(limit=10)
        assert len(runs) == 2
        # Most recent first
        assert runs[0]["run_id"] == "run-b"

    def test_list_runs_respects_limit(self, store):
        for i in range(10):
            store.emit(ExecutionEvent(run_id=f"run-{i}", event_type="start"))

        runs = store.list_runs(limit=3)
        assert len(runs) == 3


class TestEventStoreIsolation:
    """Test that different runs are isolated."""

    def test_run_isolation(self, store):
        store.emit(ExecutionEvent(run_id="run-x", event_type="event_x"))
        store.emit(ExecutionEvent(run_id="run-y", event_type="event_y"))

        x_events = store.get_run_events("run-x")
        y_events = store.get_run_events("run-y")

        assert len(x_events) == 1
        assert len(y_events) == 1
        assert x_events[0]["event_type"] == "event_x"
        assert y_events[0]["event_type"] == "event_y"

"""Tests for routing functions (V3: includes planning and reflection routes)."""
import pytest
from unittest.mock import Mock

from langgraph_browser_agent.state import BrowserAgentState
from langgraph_browser_agent.routes import (
    route_paused,
    route_consecutive_failures,
    route_stopped,
    route_completion,
    route_on_timeout_or_error,
    # V3
    route_needs_planning,
    route_reflection,
)
from langgraph_browser_agent.models import ReflectionResult


def _make_state(**overrides) -> BrowserAgentState:
    base: BrowserAgentState = {
        "task": "test",
        "browser_state_summary": None,
        "last_model_output": None,
        "last_result": None,
        "sub_goals": None,
        "current_sub_goal_index": 0,
        "plan_context": None,
    }
    base.update(overrides)
    return base


class TestRoutePaused:
    """Test route_paused function."""

    def test_route_paused_when_paused(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.state.paused = True

        result = route_paused(_make_state(), mock_agent_instance)
        assert result == "paused"

    def test_route_paused_when_not_paused(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.state.paused = False

        result = route_paused(_make_state(), mock_agent_instance)
        assert result == "not_paused"


class TestRouteConsecutiveFailures:
    """Test route_consecutive_failures function."""

    def test_route_too_many_failures(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.state.consecutive_failures = 4
        mock_agent_instance.original_agent.settings.max_failures = 3
        mock_agent_instance.original_agent.settings.final_response_after_failure = False

        result = route_consecutive_failures(_make_state(), mock_agent_instance)
        assert result == "too_many_failures"

    def test_route_ok_failures(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.state.consecutive_failures = 2
        mock_agent_instance.original_agent.settings.max_failures = 3
        mock_agent_instance.original_agent.settings.final_response_after_failure = False

        result = route_consecutive_failures(_make_state(), mock_agent_instance)
        assert result == "ok"


class TestRouteStopped:
    """Test route_stopped function."""

    def test_route_stopped_when_stopped(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.state.stopped = True

        result = route_stopped(_make_state(), mock_agent_instance)
        assert result == "stopped"

    def test_route_not_stopped(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.state.stopped = False

        result = route_stopped(_make_state(), mock_agent_instance)
        assert result == "not_stopped"


class TestRouteCompletion:
    """Test route_completion function."""

    def test_route_done_when_history_done(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.history.is_done.return_value = True

        result = route_completion(_make_state(), mock_agent_instance)
        assert result == "done"

    def test_route_continue_when_not_done(self):
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.history.is_done.return_value = False

        result = route_completion(_make_state(), mock_agent_instance)
        assert result == "continue"

    def test_route_done_when_all_sub_goals_completed(self):
        """V3: completion when all sub-goals are done."""
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.history.is_done.return_value = False

        state = _make_state(
            sub_goals=[
                {"description": "step 1", "status": "completed"},
                {"description": "step 2", "status": "completed"},
            ],
            current_sub_goal_index=2,  # past last index
        )

        result = route_completion(state, mock_agent_instance)
        assert result == "done"

    def test_route_continue_when_sub_goals_remaining(self):
        """V3: continue when sub-goals are not all done."""
        mock_agent_instance = Mock()
        mock_agent_instance.original_agent.history.is_done.return_value = False

        state = _make_state(
            sub_goals=[
                {"description": "step 1", "status": "completed"},
                {"description": "step 2", "status": "active"},
            ],
            current_sub_goal_index=1,
        )

        result = route_completion(state, mock_agent_instance)
        assert result == "continue"


class TestRouteOnTimeoutOrError:
    """Test route_on_timeout_or_error function."""

    def test_route_timeout(self):
        mock_agent_instance = Mock()
        mock_agent_instance.step_timed_out = True
        mock_agent_instance.last_error = "some error"

        result = route_on_timeout_or_error(_make_state(), mock_agent_instance)
        assert result == "timeout"

    def test_route_error(self):
        mock_agent_instance = Mock()
        mock_agent_instance.step_timed_out = False
        mock_agent_instance.last_error = "some error"

        result = route_on_timeout_or_error(_make_state(), mock_agent_instance)
        assert result == "error"

    def test_route_continue(self):
        mock_agent_instance = Mock()
        mock_agent_instance.step_timed_out = False
        mock_agent_instance.last_error = None

        result = route_on_timeout_or_error(_make_state(), mock_agent_instance)
        assert result == "continue"


# -----------------------------------------------------------------------
# V3: Planning and Reflection routes
# -----------------------------------------------------------------------

class TestRouteNeedsPlanning:
    """Test route_needs_planning function."""

    def test_needs_planning_first_entry(self):
        mock_agent = Mock()
        mock_agent.planning_done = False

        result = route_needs_planning(_make_state(), mock_agent)
        assert result == "plan"

    def test_skip_planning_when_done(self):
        mock_agent = Mock()
        mock_agent.planning_done = True

        result = route_needs_planning(_make_state(), mock_agent)
        assert result == "skip_plan"


class TestRouteReflection:
    """Test route_reflection function."""

    def test_sub_goal_met(self):
        mock_agent = Mock()
        mock_agent.reflection_result = ReflectionResult(
            sub_goal_met=True, reasoning="Criteria satisfied", should_replan=False
        )
        mock_agent.needs_replan = False

        result = route_reflection(_make_state(), mock_agent)
        assert result == "sub_goal_met"

    def test_continue_when_not_met(self):
        mock_agent = Mock()
        mock_agent.reflection_result = ReflectionResult(
            sub_goal_met=False, reasoning="Not yet", should_replan=False
        )
        mock_agent.needs_replan = False

        result = route_reflection(_make_state(), mock_agent)
        assert result == "continue"

    def test_replan_requested(self):
        mock_agent = Mock()
        mock_agent.reflection_result = ReflectionResult(
            sub_goal_met=False, reasoning="Stuck", should_replan=True
        )
        mock_agent.needs_replan = True

        result = route_reflection(_make_state(), mock_agent)
        assert result == "replan"

    def test_no_reflection_result(self):
        mock_agent = Mock()
        mock_agent.reflection_result = None
        mock_agent.needs_replan = False

        result = route_reflection(_make_state(), mock_agent)
        assert result == "continue"
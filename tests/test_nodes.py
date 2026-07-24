"""Tests for node implementations (V3: includes planner, reflector, bug fix)."""
import pytest
from unittest.mock import Mock, AsyncMock, patch
import time

from langgraph_browser_agent.state import BrowserAgentState
from langgraph_browser_agent.nodes import (
    check_paused_node,
    check_consecutive_failures_node,
    check_stopped_node,
    on_step_start_node,
    on_step_end_node,
    paused_state_actions_node,
    consecutive_failure_actions_node,
    stopped_state_actions_node,
    history_is_done_actions_node,
    check_step_timeout,
    prepare_context_node,
    get_next_action_node,
    execute_actions_node,
    evaluate_result_node,
    finalize_step_node,
    handle_error_node,
)


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


class TestCheckNodes:
    """Test check nodes (no-ops)."""

    def test_check_paused_node(self):
        state = _make_state()
        result = check_paused_node(state, Mock())
        assert result == state

    def test_check_consecutive_failures_node(self):
        state = _make_state()
        result = check_consecutive_failures_node(state, Mock())
        assert result == state

    def test_check_stopped_node(self):
        state = _make_state()
        result = check_stopped_node(state, Mock())
        assert result == state


class TestActionNodes:
    """Test action nodes."""

    @pytest.mark.asyncio
    async def test_paused_state_actions_node(self):
        mock_agent = Mock()
        mock_agent.current_step = 0
        mock_agent.original_agent.logger = Mock()
        mock_agent.original_agent._external_pause_event = AsyncMock()
        mock_agent.signal_handler = Mock()
        mock_agent.signal_handler.reset = Mock()
        mock_agent.event_store = None

        state = _make_state()
        await paused_state_actions_node(state, mock_agent)

        mock_agent.original_agent._external_pause_event.wait.assert_called_once()
        mock_agent.signal_handler.reset.assert_called_once()

    def test_consecutive_failure_actions_node(self):
        mock_agent = Mock()
        mock_agent.original_agent.logger = Mock()
        mock_agent.original_agent.settings.max_failures = 3
        mock_agent.event_store = None

        state = _make_state()
        consecutive_failure_actions_node(state, mock_agent)
        assert mock_agent.ended_due_to_break is True

    def test_stopped_state_actions_node(self):
        mock_agent = Mock()
        mock_agent.original_agent.logger = Mock()
        mock_agent.event_store = None

        state = _make_state()
        stopped_state_actions_node(state, mock_agent)
        assert mock_agent.ended_due_to_break is True


class TestStepTimeout:
    """Test step timeout functionality."""

    def test_check_step_timeout_no_timeout(self):
        mock_agent = Mock()
        mock_agent.current_step = 0
        mock_agent.step_timed_out = False
        mock_agent.original_agent.step_start_time = time.time() - 10
        mock_agent.original_agent.settings.step_timeout = 30
        mock_agent.event_store = None

        state = _make_state()
        result = check_step_timeout(state, mock_agent)

        assert result is False
        assert mock_agent.step_timed_out is False

    def test_check_step_timeout_with_timeout(self):
        mock_agent = Mock()
        mock_agent.current_step = 0
        mock_agent.step_timed_out = False
        mock_agent.original_agent.step_start_time = time.time() - 40
        mock_agent.original_agent.settings.step_timeout = 30
        mock_agent.original_agent.logger = Mock()
        mock_agent.original_agent.state.consecutive_failures = 0
        mock_agent.event_store = None
        mock_agent.run_id = "test"

        state = _make_state()
        result = check_step_timeout(state, mock_agent)

        assert result is True
        assert mock_agent.step_timed_out is True
        assert mock_agent.original_agent.state.consecutive_failures == 1


class TestStepTimedOutReset:
    """V3: Test that step_timed_out is reset at the start of each step."""

    @pytest.mark.asyncio
    async def test_on_step_start_resets_timed_out(self):
        """Critical bug fix: step_timed_out must be cleared each step."""
        mock_agent = Mock()
        mock_agent.step_timed_out = True  # Simulating stale timeout from previous step
        mock_agent.last_error = "stale error"
        mock_agent.on_step_start = None
        mock_agent.event_store = None

        state = _make_state()
        await on_step_start_node(state, mock_agent)

        assert mock_agent.step_timed_out is False
        assert mock_agent.last_error is None

    @pytest.mark.asyncio
    async def test_on_step_start_calls_hook(self):
        """Verify on_step_start hook is still called after reset."""
        mock_agent = Mock()
        mock_agent.step_timed_out = False
        mock_agent.last_error = None
        mock_agent.on_step_start = AsyncMock()
        mock_agent.event_store = None

        state = _make_state()
        await on_step_start_node(state, mock_agent)

        mock_agent.on_step_start.assert_called_once_with(mock_agent.original_agent)


class TestOnStepEnd:
    """Test on_step_end_node."""

    @pytest.mark.asyncio
    async def test_on_step_end_with_hook(self):
        mock_agent = Mock()
        mock_agent.on_step_end = AsyncMock()
        mock_agent.event_store = None

        state = _make_state()
        await on_step_end_node(state, mock_agent)

        mock_agent.on_step_end.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_step_end_without_hook(self):
        mock_agent = Mock()
        mock_agent.on_step_end = None
        mock_agent.event_store = None

        state = _make_state()
        result = await on_step_end_node(state, mock_agent)
        assert result == state
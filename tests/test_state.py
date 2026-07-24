"""Tests for the BrowserAgentState schema (V3)."""
import pytest

from langgraph_browser_agent.state import BrowserAgentState


class TestBrowserAgentState:
    """Test BrowserAgentState TypedDict structure."""

    def test_state_has_core_fields(self):
        """Verify original core fields exist."""
        state: BrowserAgentState = {
            "task": "test task",
            "browser_state_summary": None,
            "last_model_output": None,
            "last_result": None,
            "sub_goals": None,
            "current_sub_goal_index": 0,
            "plan_context": None,
        }

        assert state["task"] == "test task"
        assert state["browser_state_summary"] is None
        assert state["last_model_output"] is None
        assert state["last_result"] is None

    def test_state_has_planning_fields(self):
        """V3: Verify planning/reflection fields exist."""
        state: BrowserAgentState = {
            "task": "test",
            "browser_state_summary": None,
            "last_model_output": None,
            "last_result": None,
            "sub_goals": [
                {"description": "Navigate", "success_criteria": "Page loaded", "status": "active"}
            ],
            "current_sub_goal_index": 0,
            "plan_context": "Similar past task found",
        }

        assert len(state["sub_goals"]) == 1
        assert state["current_sub_goal_index"] == 0
        assert state["plan_context"] == "Similar past task found"

    def test_state_sub_goals_can_be_none(self):
        """Sub-goals should be None before planning."""
        state: BrowserAgentState = {
            "task": "test",
            "browser_state_summary": None,
            "last_model_output": None,
            "last_result": None,
            "sub_goals": None,
            "current_sub_goal_index": 0,
            "plan_context": None,
        }

        assert state["sub_goals"] is None

    def test_all_keys_present(self):
        """Verify all expected keys are in the TypedDict."""
        expected_keys = {
            "task",
            "browser_state_summary",
            "last_model_output",
            "last_result",
            "sub_goals",
            "current_sub_goal_index",
            "plan_context",
        }

        state: BrowserAgentState = {
            "task": "test",
            "browser_state_summary": None,
            "last_model_output": None,
            "last_result": None,
            "sub_goals": None,
            "current_sub_goal_index": 0,
            "plan_context": None,
        }

        assert set(state.keys()) == expected_keys

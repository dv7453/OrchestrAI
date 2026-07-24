"""Conditional routers for the LangGraph browser agent.

Each router reads agent_instance (closure-injected) rather than just state,
because most control flags live on the wrapped browser-use Agent, not in
LangGraph state.

V3 additions:
- route_needs_planning: determines whether the planner should run
- route_reflection: routes based on reflector output (sub-goal met / continue / replan)
"""

from .state import BrowserAgentState


def route_paused(state: BrowserAgentState, agent_instance) -> str:
    agent = agent_instance.original_agent
    if agent.state.paused:
        return "paused"
    else:
        return "not_paused"


def route_consecutive_failures(state: BrowserAgentState, agent_instance) -> str:
    agent = agent_instance.original_agent
    if agent.state.consecutive_failures >= agent.settings.max_failures + int(agent.settings.final_response_after_failure):
        return "too_many_failures"
    else:
        return "ok"


def route_stopped(state: BrowserAgentState, agent_instance) -> str:
    agent = agent_instance.original_agent
    if agent.state.stopped:
        return "stopped"
    else:
        return "not_stopped"


def route_completion(state: BrowserAgentState, agent_instance) -> str:
    agent = agent_instance.original_agent

    # V3: also check if all sub-goals are completed
    sub_goals = state.get("sub_goals")
    idx = state.get("current_sub_goal_index", 0)
    if sub_goals and idx >= len(sub_goals):
        return "done"

    if agent.history.is_done():
        return "done"
    else:
        return "continue"


def route_on_timeout_or_error(state: BrowserAgentState, agent_instance) -> str:
    if agent_instance.step_timed_out:
        return "timeout"
    elif agent_instance.last_error is not None:
        return "error"
    else:
        return "continue"


# ---------------------------------------------------------------------------
# V3 routers
# ---------------------------------------------------------------------------

def route_needs_planning(state: BrowserAgentState, agent_instance) -> str:
    """Determine whether the planner node should execute.

    Returns "plan" on first entry (planning_done is False) or when the
    reflector has requested re-planning.  Returns "skip_plan" when the
    plan already exists and the agent is looping through step iterations.
    """
    if not getattr(agent_instance, "planning_done", False):
        return "plan"
    return "skip_plan"


def route_reflection(state: BrowserAgentState, agent_instance) -> str:
    """Route based on the reflector's evaluation.

    Returns:
        "sub_goal_met" — sub-goal achieved, proceed to on_step_end
        "replan"       — reflector requested re-planning
        "continue"     — sub-goal not yet met, proceed to on_step_end (loop)
    """
    result = getattr(agent_instance, "reflection_result", None)
    if result is None:
        # Reflector was skipped (no plan) or errored — continue normally
        return "continue"

    if getattr(agent_instance, "needs_replan", False):
        return "replan"

    if result.sub_goal_met:
        return "sub_goal_met"

    return "continue"

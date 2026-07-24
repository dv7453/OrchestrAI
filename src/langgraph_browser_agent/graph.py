"""LangGraph StateGraph wiring for the browser agent.

V3 topology:
    START → check_paused → [paused_state_actions →] check_consecutive_failures
          → [consecutive_failure_actions → END |] check_stopped
          → [stopped_state_actions → END |] planning_gate
          → [planner →] on_step_start → prepare_context
          → [handle_error → finalize_step |] get_next_action
          → [handle_error → finalize_step |] execute_actions
          → [handle_error → finalize_step |] evaluate_result
          → finalize_step → reflector
          → [replan → planner |] on_step_end
          → [history_is_done_actions → END |] check_paused (LOOP)

New nodes (V3): planner, planning_gate, reflector
New routes (V3): route_needs_planning, route_reflection
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import BrowserAgentState
from .nodes import (
    check_paused_node,
    check_consecutive_failures_node,
    check_stopped_node,
    paused_state_actions_node,
    consecutive_failure_actions_node,
    stopped_state_actions_node,
    history_is_done_actions_node,
    on_step_start_node,
    on_step_end_node,
    prepare_context_node,
    get_next_action_node,
    execute_actions_node,
    evaluate_result_node,
    finalize_step_node,
    handle_error_node,
    # V3
    planner_node,
    reflector_node,
)
from .routes import (
    route_paused,
    route_consecutive_failures,
    route_stopped,
    route_completion,
    route_on_timeout_or_error,
    # V3
    route_needs_planning,
    route_reflection,
)


def create_browser_agent_graph(agent_instance):
    """Create LangGraph workflow for the browser agent step loop.

    V3: adds planner (with planning_gate), reflector, and associated
    conditional edges for the Plan → Execute → Reflect architecture.
    """
    workflow = StateGraph(BrowserAgentState)

    # ------------------------------------------------------------------
    # Closure wrappers — inject agent_instance into every node/route
    # ------------------------------------------------------------------

    # --- check nodes (sync) ---
    def check_paused_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return check_paused_node(state, agent_instance)

    def check_consecutive_failures_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return check_consecutive_failures_node(state, agent_instance)

    def check_stopped_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return check_stopped_node(state, agent_instance)

    # --- terminal / control nodes ---
    async def paused_state_actions_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await paused_state_actions_node(state, agent_instance)

    def consecutive_failure_actions_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return consecutive_failure_actions_node(state, agent_instance)

    def stopped_state_actions_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return stopped_state_actions_node(state, agent_instance)

    async def history_is_done_actions_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await history_is_done_actions_node(state, agent_instance)

    # --- lifecycle hooks ---
    async def on_step_start_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await on_step_start_node(state, agent_instance)

    async def on_step_end_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await on_step_end_node(state, agent_instance)

    # --- core step nodes ---
    async def prepare_context_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await prepare_context_node(state, agent_instance)

    async def get_next_action_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await get_next_action_node(state, agent_instance)

    async def execute_actions_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await execute_actions_node(state, agent_instance)

    async def evaluate_result_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await evaluate_result_node(state, agent_instance)

    async def finalize_step_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await finalize_step_node(state, agent_instance)

    async def handle_error_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await handle_error_node(state, agent_instance)

    # --- V3: planner & reflector ---
    async def planner_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await planner_node(state, agent_instance)

    async def reflector_node_with_agent(state: BrowserAgentState) -> BrowserAgentState:
        return await reflector_node(state, agent_instance)

    # Planning gate — a no-op node; routing after it decides plan vs skip
    def planning_gate_node(state: BrowserAgentState) -> BrowserAgentState:
        return state

    # ------------------------------------------------------------------
    # Route wrappers
    # ------------------------------------------------------------------
    def route_paused_with_agent(state: BrowserAgentState) -> str:
        return route_paused(state, agent_instance)

    def route_stopped_with_agent(state: BrowserAgentState) -> str:
        return route_stopped(state, agent_instance)

    def route_consecutive_failures_with_agent(state: BrowserAgentState) -> str:
        return route_consecutive_failures(state, agent_instance)

    def route_completion_with_agent(state: BrowserAgentState) -> str:
        return route_completion(state, agent_instance)

    def route_on_timeout_or_error_with_agent(state: BrowserAgentState) -> str:
        return route_on_timeout_or_error(state, agent_instance)

    def route_needs_planning_with_agent(state: BrowserAgentState) -> str:
        return route_needs_planning(state, agent_instance)

    def route_reflection_with_agent(state: BrowserAgentState) -> str:
        return route_reflection(state, agent_instance)

    # ------------------------------------------------------------------
    # Register nodes
    # ------------------------------------------------------------------
    workflow.add_node("check_paused", check_paused_node_with_agent)
    workflow.add_node("check_consecutive_failures", check_consecutive_failures_node_with_agent)
    workflow.add_node("check_stopped", check_stopped_node_with_agent)
    workflow.add_node("paused_state_actions", paused_state_actions_node_with_agent)
    workflow.add_node("consecutive_failure_actions", consecutive_failure_actions_node_with_agent)
    workflow.add_node("stopped_state_actions", stopped_state_actions_node_with_agent)
    workflow.add_node("history_is_done_actions", history_is_done_actions_node_with_agent)
    workflow.add_node("on_step_start", on_step_start_node_with_agent)
    workflow.add_node("on_step_end", on_step_end_node_with_agent)
    workflow.add_node("prepare_context", prepare_context_node_with_agent)
    workflow.add_node("get_next_action", get_next_action_node_with_agent)
    workflow.add_node("execute_actions", execute_actions_node_with_agent)
    workflow.add_node("evaluate_result", evaluate_result_node_with_agent)
    workflow.add_node("finalize_step", finalize_step_node_with_agent)
    workflow.add_node("handle_error", handle_error_node_with_agent)
    # V3
    workflow.add_node("planning_gate", planning_gate_node)
    workflow.add_node("planner", planner_node_with_agent)
    workflow.add_node("reflector", reflector_node_with_agent)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    workflow.set_entry_point("check_paused")

    # ------------------------------------------------------------------
    # Edges — guard phase
    # ------------------------------------------------------------------
    workflow.add_conditional_edges(
        "check_paused",
        route_paused_with_agent,
        {
            "paused": "paused_state_actions",
            "not_paused": "check_consecutive_failures",
        },
    )

    workflow.add_edge("paused_state_actions", "check_consecutive_failures")

    workflow.add_conditional_edges(
        "check_consecutive_failures",
        route_consecutive_failures_with_agent,
        {
            "too_many_failures": "consecutive_failure_actions",
            "ok": "check_stopped",
        },
    )

    workflow.add_edge("consecutive_failure_actions", END)

    workflow.add_conditional_edges(
        "check_stopped",
        route_stopped_with_agent,
        {
            "stopped": "stopped_state_actions",
            "not_stopped": "planning_gate",       # V3: go to planning gate
        },
    )

    workflow.add_edge("stopped_state_actions", END)

    # ------------------------------------------------------------------
    # Edges — V3 planning gate
    # ------------------------------------------------------------------
    workflow.add_conditional_edges(
        "planning_gate",
        route_needs_planning_with_agent,
        {
            "plan": "planner",
            "skip_plan": "on_step_start",
        },
    )

    workflow.add_edge("planner", "on_step_start")

    # ------------------------------------------------------------------
    # Edges — step pipeline
    # ------------------------------------------------------------------
    workflow.add_edge("on_step_start", "prepare_context")

    workflow.add_conditional_edges(
        "prepare_context",
        route_on_timeout_or_error_with_agent,
        {
            "timeout": "on_step_end",
            "error": "handle_error",
            "continue": "get_next_action",
        },
    )

    workflow.add_conditional_edges(
        "get_next_action",
        route_on_timeout_or_error_with_agent,
        {
            "timeout": "on_step_end",
            "error": "handle_error",
            "continue": "execute_actions",
        },
    )

    workflow.add_conditional_edges(
        "execute_actions",
        route_on_timeout_or_error_with_agent,
        {
            "timeout": "on_step_end",
            "error": "handle_error",
            "continue": "evaluate_result",
        },
    )

    workflow.add_conditional_edges(
        "evaluate_result",
        route_on_timeout_or_error_with_agent,
        {
            "timeout": "on_step_end",
            "error": "handle_error",
            "continue": "finalize_step",
        },
    )

    workflow.add_edge("handle_error", "finalize_step")

    # ------------------------------------------------------------------
    # Edges — V3 reflection after finalize
    # ------------------------------------------------------------------
    workflow.add_edge("finalize_step", "reflector")

    workflow.add_conditional_edges(
        "reflector",
        route_reflection_with_agent,
        {
            "sub_goal_met": "on_step_end",
            "continue": "on_step_end",
            "replan": "planner",           # V3: re-plan on failure
        },
    )

    # ------------------------------------------------------------------
    # Edges — completion check / loop
    # ------------------------------------------------------------------
    workflow.add_conditional_edges(
        "on_step_end",
        route_completion_with_agent,
        {
            "done": "history_is_done_actions",
            "continue": "check_paused",     # LOOP back to start
        },
    )

    workflow.add_edge("history_is_done_actions", END)

    # Compile without checkpointer to avoid serialization issues
    return workflow.compile()


def create_standalone_graph():
    """Create a standalone graph for LangGraph Studio visualization.

    Builds a comprehensive Mock agent_instance so Studio can draw the
    full V3 graph (including planner/reflector) without a real
    browser/LLM.
    """
    from unittest.mock import Mock, AsyncMock

    mock_agent = Mock()

    # --- wrapper-level attributes ---
    mock_agent.current_step = 0
    mock_agent.max_steps = 10
    mock_agent.step_info = None
    mock_agent.last_error = None
    mock_agent.ended_due_to_break = False
    mock_agent.step_timed_out = False
    mock_agent.signal_handler = Mock()
    mock_agent.signal_handler.reset = Mock()

    # V3 planning/reflection attributes
    mock_agent.planning_done = False
    mock_agent.needs_replan = False
    mock_agent.replan_count = 0
    mock_agent.reflection_result = None
    mock_agent.event_store = None       # no event store for Studio
    mock_agent.run_id = "studio-mock"

    # --- original agent ---
    mock_agent.original_agent = Mock()

    mock_agent.original_agent.settings = Mock()
    mock_agent.original_agent.settings.max_failures = 3
    mock_agent.original_agent.settings.final_response_after_failure = False
    mock_agent.original_agent.settings.step_timeout = 30

    mock_agent.original_agent.state = Mock()
    mock_agent.original_agent.state.paused = False
    mock_agent.original_agent.state.stopped = False
    mock_agent.original_agent.state.consecutive_failures = 0
    mock_agent.original_agent.state.last_result = []

    # Make history.is_done() return True after a few steps
    call_count = 0

    def mock_is_done():
        nonlocal call_count
        call_count += 1
        return call_count >= 3

    mock_agent.original_agent.history = Mock()
    mock_agent.original_agent.history.is_done = Mock(side_effect=mock_is_done)

    mock_agent.original_agent.logger = Mock()
    mock_agent.original_agent.logger.debug = Mock()
    mock_agent.original_agent.logger.error = Mock()
    mock_agent.original_agent.logger.info = Mock()

    mock_agent.original_agent.step_start_time = 0
    mock_agent.original_agent._prepare_context = AsyncMock(return_value=Mock())
    mock_agent.original_agent._get_next_action = AsyncMock()
    mock_agent.original_agent._execute_actions = AsyncMock()
    mock_agent.original_agent._post_process = AsyncMock()
    mock_agent.original_agent._finalize = AsyncMock()
    mock_agent.original_agent._handle_step_error = AsyncMock()
    mock_agent.original_agent.log_completion = AsyncMock()
    mock_agent.original_agent.register_done_callback = None

    mock_agent.on_step_start = None
    mock_agent.on_step_end = None

    mock_agent.original_agent._external_pause_event = Mock()
    mock_agent.original_agent._external_pause_event.wait = AsyncMock()

    # V3: mock LLM for planner/reflector
    mock_llm_response = Mock()
    mock_llm_response.content = '{"reasoning": "demo", "sub_goals": [{"description": "Navigate to site", "success_criteria": "Page loaded"}]}'
    mock_agent.llm = AsyncMock(return_value=mock_llm_response)

    return create_browser_agent_graph(mock_agent)

"""Node implementations for the LangGraph browser agent.

Each node receives (state, agent_instance) and returns the updated state.
The agent_instance is injected via closures in graph.py.

V3 additions:
- planner_node: decomposes tasks into sub-goals via structured LLM output
- reflector_node: evaluates sub-goal completion after each step
- Event emission to EventStore for full execution tracing
"""

import json
import time
import inspect

from .state import BrowserAgentState
from .models import SubGoal, TaskPlan, ReflectionResult

from browser_use.agent.views import AgentStepInfo, ActionResult


# ---------------------------------------------------------------------------
# Event emission helper
# ---------------------------------------------------------------------------

def _emit(agent, event_type: str, node_name: str | None = None, **payload):
    """Emit an execution event if an EventStore is attached."""
    store = getattr(agent, "event_store", None)
    if store is None:
        return
    from .event_store import ExecutionEvent

    store.emit(
        ExecutionEvent(
            run_id=getattr(agent, "run_id", "unknown"),
            event_type=event_type,
            step_number=getattr(agent, "current_step", 0),
            node_name=node_name,
            payload=payload,
        )
    )


# ---------------------------------------------------------------------------
# Guard / check nodes (no-ops — routing happens on conditional edges)
# ---------------------------------------------------------------------------

def check_paused_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    return state


def check_consecutive_failures_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    return state


def check_stopped_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    return state


# ---------------------------------------------------------------------------
# Step lifecycle hooks
# ---------------------------------------------------------------------------

async def on_step_start_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    # Reset per-step flags to prevent stale state from previous steps.
    # Critical fix: step_timed_out was never cleared, causing a death spiral
    # where every step after a single timeout was routed as "timeout".
    agent_instance.step_timed_out = False
    agent_instance.last_error = None

    _emit(agent_instance, "node_entered", "on_step_start")

    if hasattr(agent_instance, 'on_step_start') and agent_instance.on_step_start is not None:
        await agent_instance.on_step_start(agent_instance.original_agent)

    _emit(agent_instance, "node_exited", "on_step_start")
    return state


async def on_step_end_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    _emit(agent_instance, "node_entered", "on_step_end")

    if hasattr(agent_instance, 'on_step_end') and agent_instance.on_step_end is not None:
        await agent_instance.on_step_end(agent_instance.original_agent)

    _emit(agent_instance, "node_exited", "on_step_end")
    return state


# ---------------------------------------------------------------------------
# Terminal / control nodes
# ---------------------------------------------------------------------------

async def paused_state_actions_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    agent = agent_instance.original_agent
    agent.logger.debug(f'⏸️ Step {agent_instance.current_step}: Agent paused, waiting to resume...')
    _emit(agent_instance, "node_entered", "paused_state_actions")
    await agent._external_pause_event.wait()
    agent_instance.signal_handler.reset()
    _emit(agent_instance, "node_exited", "paused_state_actions")
    return state


def consecutive_failure_actions_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    agent = agent_instance.original_agent
    agent.logger.error(f'❌ Stopping due to {agent.settings.max_failures} consecutive failures')
    agent_instance.ended_due_to_break = True
    _emit(agent_instance, "node_exited", "consecutive_failure_actions", reason="max_consecutive_failures")
    return state


def stopped_state_actions_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    agent = agent_instance.original_agent
    agent.logger.info('🛑 Agent stopped')
    agent_instance.ended_due_to_break = True
    _emit(agent_instance, "node_exited", "stopped_state_actions", reason="user_stopped")
    return state


async def history_is_done_actions_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    agent = agent_instance.original_agent
    agent.logger.debug(f'🎯 Task completed after {agent_instance.current_step + 1} steps!')
    _emit(agent_instance, "node_entered", "history_is_done_actions")

    await agent.log_completion()
    if agent.register_done_callback:
        if inspect.iscoroutinefunction(agent.register_done_callback):
            await agent.register_done_callback(agent.history)
        else:
            agent.register_done_callback(agent.history)
    agent_instance.ended_due_to_break = True

    _emit(agent_instance, "node_exited", "history_is_done_actions", steps=agent_instance.current_step + 1)
    return state


# ---------------------------------------------------------------------------
# Step timeout helper
# ---------------------------------------------------------------------------

def check_step_timeout(state: BrowserAgentState, agent) -> bool:
    elapsed_time = time.time() - agent.original_agent.step_start_time
    if elapsed_time > agent.original_agent.settings.step_timeout:
        error_msg = f'Step {agent.current_step + 1} timed out after {agent.original_agent.settings.step_timeout} seconds'
        agent.original_agent.logger.error(f'⏰ {error_msg}')
        agent.original_agent.state.consecutive_failures += 1
        agent.original_agent.state.last_result = [ActionResult(error=error_msg)]
        agent.step_timed_out = True
        _emit(agent, "timeout", "check_step_timeout", message=error_msg)
        return True
    return False


# ---------------------------------------------------------------------------
# Planner node (V3)
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are a task planner for a browser automation agent.

Given a task, decompose it into an ordered list of sub-goals. Each sub-goal should be
a concrete, observable objective that the browser agent can accomplish in a few steps.

For each sub-goal, provide:
- description: what the agent should do
- success_criteria: an observable condition on the web page that confirms this sub-goal
  is complete (e.g., "the downloads page is visible", "the form shows a success message")

Return your plan as JSON matching this schema:
{
  "reasoning": "brief explanation of your decomposition strategy",
  "sub_goals": [
    {"description": "...", "success_criteria": "..."},
    ...
  ]
}

Keep sub-goals concrete and few (2-6 for most tasks). Do not create trivially small
sub-goals like "open browser" — start with meaningful navigation or interaction."""


async def planner_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    """Decompose the task into sub-goals using a structured LLM call."""
    _emit(agent_instance, "node_entered", "planner")
    agent = agent_instance.original_agent

    task = state["task"]
    plan_context = state.get("plan_context") or ""

    prompt_parts = [PLANNER_SYSTEM_PROMPT]
    if plan_context:
        prompt_parts.append(f"\n{plan_context}")

    # If replanning, include remaining sub-goals and current state
    if agent_instance.needs_replan and state.get("sub_goals"):
        remaining = state["sub_goals"][state.get("current_sub_goal_index", 0):]
        prompt_parts.append(
            f"\nThe previous plan partially failed. Remaining sub-goals were: "
            f"{json.dumps(remaining)}\n"
            f"Adjust the plan based on the current browser state."
        )

    prompt_parts.append(f"\n## Task\n{task}")

    system_msg = "\n".join(prompt_parts)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [
            SystemMessage(content=system_msg),
            HumanMessage(content=f"Create a step-by-step plan for this task: {task}"),
        ]

        response = await agent_instance.llm.ainvoke(messages)
        response_text = response.content if hasattr(response, "content") else str(response)

        # Parse JSON from the response (handle markdown code blocks)
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0]
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0]

        plan_data = json.loads(json_text.strip())
        plan = TaskPlan(**plan_data)

        # Convert to serialisable dicts for LangGraph state
        sub_goal_dicts = [sg.model_dump() for sg in plan.sub_goals]
        state["sub_goals"] = sub_goal_dicts
        state["current_sub_goal_index"] = 0

        # Mark the first sub-goal as active
        if sub_goal_dicts:
            sub_goal_dicts[0]["status"] = "active"

        agent_instance.needs_replan = False
        agent_instance.replan_count = getattr(agent_instance, "replan_count", 0) + (
            1 if agent_instance.needs_replan else 0
        )

        agent.logger.info(f"📋 Plan created with {len(sub_goal_dicts)} sub-goals")
        for i, sg in enumerate(sub_goal_dicts, 1):
            agent.logger.debug(f"  {i}. {sg['description']}")

        _emit(
            agent_instance,
            "plan_created",
            "planner",
            reasoning=plan.reasoning,
            sub_goals=sub_goal_dicts,
        )

    except Exception as e:
        agent.logger.error(f"❌ Planner failed: {e}. Proceeding without plan.")
        # Graceful degradation: create a single catch-all sub-goal
        state["sub_goals"] = [
            {
                "description": task,
                "success_criteria": "The task is fully completed",
                "status": "active",
            }
        ]
        state["current_sub_goal_index"] = 0
        _emit(agent_instance, "error", "planner", message=str(e))

    agent_instance.planning_done = True
    _emit(agent_instance, "node_exited", "planner")
    return state


# ---------------------------------------------------------------------------
# Reflector node (V3)
# ---------------------------------------------------------------------------

REFLECTOR_SYSTEM_PROMPT = """You are evaluating whether a browser automation sub-goal has been achieved.

Given the current browser page state and the sub-goal's success criteria, determine:
1. Whether the success criteria are satisfied based on observable page content
2. Whether the plan should be revised if the agent seems stuck

Return your evaluation as JSON:
{
  "sub_goal_met": true/false,
  "reasoning": "brief explanation",
  "should_replan": true/false
}

Be strict: only mark sub_goal_met as true if there is clear evidence in the page
state that the criteria are satisfied."""


async def reflector_node(state: BrowserAgentState, agent_instance) -> BrowserAgentState:
    """Evaluate whether the current sub-goal has been achieved."""
    _emit(agent_instance, "node_entered", "reflector")
    agent = agent_instance.original_agent

    sub_goals = state.get("sub_goals")
    idx = state.get("current_sub_goal_index", 0)

    # No plan or past last sub-goal — let the standard completion check handle it
    if not sub_goals or idx >= len(sub_goals):
        agent_instance.reflection_result = None
        _emit(agent_instance, "node_exited", "reflector", skipped=True)
        return state

    current_sg = sub_goals[idx]
    browser_summary = state.get("browser_state_summary")

    # Build a concise page state description for the reflector
    page_info = "No browser state available."
    if browser_summary:
        url = getattr(browser_summary, "url", "unknown")
        title = getattr(browser_summary, "title", "unknown")
        page_info = f"Current URL: {url}\nPage title: {title}"

    try:
        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [
            SystemMessage(content=REFLECTOR_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"## Current Sub-Goal\n"
                    f"Description: {current_sg['description']}\n"
                    f"Success Criteria: {current_sg['success_criteria']}\n\n"
                    f"## Current Browser State\n{page_info}\n\n"
                    f"Has this sub-goal been achieved?"
                )
            ),
        ]

        response = await agent_instance.llm.ainvoke(messages)
        response_text = response.content if hasattr(response, "content") else str(response)

        # Parse JSON
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0]
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0]

        result = ReflectionResult(**json.loads(json_text.strip()))

        if result.sub_goal_met:
            # Mark current sub-goal as completed
            sub_goals[idx]["status"] = "completed"
            agent.logger.info(f"✅ Sub-goal {idx + 1} completed: {current_sg['description']}")

            # Advance to next sub-goal
            next_idx = idx + 1
            state["current_sub_goal_index"] = next_idx
            if next_idx < len(sub_goals):
                sub_goals[next_idx]["status"] = "active"
                agent.logger.info(f"➡️ Moving to sub-goal {next_idx + 1}: {sub_goals[next_idx]['description']}")

            _emit(
                agent_instance,
                "sub_goal_completed",
                "reflector",
                sub_goal_index=idx,
                description=current_sg["description"],
                reasoning=result.reasoning,
            )

            # Reset consecutive failures on sub-goal progress
            agent.state.consecutive_failures = 0

        else:
            agent.logger.debug(
                f"🔄 Sub-goal {idx + 1} not yet met: {result.reasoning}"
            )

        agent_instance.reflection_result = result
        agent_instance.needs_replan = result.should_replan and not result.sub_goal_met

        if agent_instance.needs_replan:
            agent_instance.planning_done = False
            agent.logger.info(f"🔀 Reflector requests re-planning: {result.reasoning}")
            _emit(agent_instance, "replan_requested", "reflector", reasoning=result.reasoning)

        _emit(
            agent_instance,
            "reflection",
            "reflector",
            sub_goal_met=result.sub_goal_met,
            reasoning=result.reasoning,
            should_replan=result.should_replan,
        )

    except Exception as e:
        agent.logger.error(f"❌ Reflector failed: {e}. Continuing without reflection.")
        agent_instance.reflection_result = None
        _emit(agent_instance, "error", "reflector", message=str(e))

    _emit(agent_instance, "node_exited", "reflector")
    return state


# ---------------------------------------------------------------------------
# Core step nodes (delegate into browser-use)
# ---------------------------------------------------------------------------

async def prepare_context_node(state: BrowserAgentState, agent) -> BrowserAgentState:
    agent.original_agent.logger.debug(f'🚶 Starting step {agent.current_step + 1}/{agent.max_steps}...')
    agent.original_agent.step_start_time = time.time()
    _emit(agent, "node_entered", "prepare_context")
    print(f"🔄 Step {agent.current_step}: Preparing context...")
    try:
        step_info = AgentStepInfo(
            step_number=agent.current_step,
            max_steps=agent.max_steps
        )
        browser_state_summary = await agent.original_agent._prepare_context(step_info)
        state['browser_state_summary'] = browser_state_summary
        agent.step_info = step_info
        agent.last_error = None
        print(f"✅ Context prepared for step {agent.current_step}")
    except Exception as e:
        agent.last_error = str(e)
        print(f"❌ Error in prepare_context for step {agent.current_step}: {e}")
        _emit(agent, "error", "prepare_context", message=str(e))
    if check_step_timeout(state, agent):
        print(f"⏰ Step {agent.current_step} timed out in prepare_context")
    _emit(agent, "node_exited", "prepare_context")
    return state


async def get_next_action_node(state: BrowserAgentState, agent) -> BrowserAgentState:
    _emit(agent, "node_entered", "get_next_action")
    print(f"🤖 Step {agent.current_step}: Getting next action from LLM...")
    try:
        await agent.original_agent._get_next_action(state['browser_state_summary'])
        agent.last_error = None
        state['last_model_output'] = agent.original_agent.state.last_model_output
        print(f"✅ LLM response received for step {agent.current_step}")
    except Exception as e:
        agent.last_error = str(e)
        print(f"❌ Error in get_next_action for step {agent.current_step}: {e}")
        _emit(agent, "error", "get_next_action", message=str(e))
    if check_step_timeout(state, agent):
        print(f"⏰ Step {agent.current_step} timed out in get_next_action")
    _emit(agent, "node_exited", "get_next_action")
    return state


async def execute_actions_node(state: BrowserAgentState, agent) -> BrowserAgentState:
    _emit(agent, "node_entered", "execute_actions")
    print(f"⚡ Step {agent.current_step}: Executing actions...")
    try:
        await agent.original_agent._execute_actions()
        agent.last_error = None
        state['last_result'] = agent.original_agent.state.last_result
        print(f"✅ Actions executed for step {agent.current_step}")
    except Exception as e:
        agent.last_error = str(e)
        print(f"❌ Error in execute_actions for step {agent.current_step}: {e}")
        _emit(agent, "error", "execute_actions", message=str(e))
    if check_step_timeout(state, agent):
        print(f"⏰ Step {agent.current_step} timed out in execute_actions")
    _emit(agent, "node_exited", "execute_actions")
    return state


async def evaluate_result_node(state: BrowserAgentState, agent) -> BrowserAgentState:
    _emit(agent, "node_entered", "evaluate_result")
    print(f"📊 Step {agent.current_step}: Evaluating result...")
    try:
        await agent.original_agent._post_process()
        agent.last_error = None
        print(f"✅ Result evaluated for step {agent.current_step}")
    except Exception as e:
        agent.last_error = str(e)
        print(f"❌ Error in evaluate_result for step {agent.current_step}: {e}")
        _emit(agent, "error", "evaluate_result", message=str(e))
    if check_step_timeout(state, agent):
        print(f"⏰ Step {agent.current_step} timed out in evaluate_result")
    _emit(agent, "node_exited", "evaluate_result")
    return state


async def finalize_step_node(state: BrowserAgentState, agent) -> BrowserAgentState:
    _emit(agent, "node_entered", "finalize_step")
    print(f"🔚 Step {agent.current_step}: Finalizing step...")
    await agent.original_agent._finalize(state['browser_state_summary'])
    agent.current_step += 1
    print(f"✅ Step {agent.current_step - 1} finalized, next step will be {agent.current_step}")
    if check_step_timeout(state, agent):
        print(f"⏰ Step {agent.current_step - 1} timed out in finalize_step")
    _emit(agent, "node_exited", "finalize_step", step_completed=agent.current_step - 1)
    return state


async def handle_error_node(state: BrowserAgentState, agent) -> BrowserAgentState:
    _emit(agent, "node_entered", "handle_error")
    print(f"❌ Step {agent.current_step}: Handling error...")
    try:
        error = Exception(agent.last_error) if agent.last_error else Exception("Unknown error")
        await agent.original_agent._handle_step_error(error)
        print(f"✅ Error handled for step {agent.current_step}")
    except Exception as e:
        print(f"❌ Error in handle_error_node for step {agent.current_step}: {e}")
    _emit(agent, "node_exited", "handle_error")
    return state
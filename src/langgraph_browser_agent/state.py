from typing import TypedDict, Optional, List, Any

from browser_use.agent.views import ActionResult, AgentOutput
from browser_use.browser.views import BrowserStateSummary


class BrowserAgentState(TypedDict):
    """State schema for LangGraph browser agent.

    Core fields are passed through the original step pipeline.
    Planning fields support the Plan → Execute → Reflect architecture.
    """

    # --- Core (original) ---
    task: str
    browser_state_summary: Optional[BrowserStateSummary]
    last_model_output: Optional[AgentOutput]
    last_result: Optional[List[ActionResult]]

    # --- Planning / Reflection ---
    sub_goals: Optional[List[dict]]          # serialised SubGoal dicts
    current_sub_goal_index: int              # pointer into sub_goals
    plan_context: Optional[str]              # retrieved episodic memory text


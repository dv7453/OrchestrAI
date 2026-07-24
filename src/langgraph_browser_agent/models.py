"""Pydantic models for the planning and reflection system."""

from typing import Optional
from pydantic import BaseModel, Field


class SubGoal(BaseModel):
    """A single sub-goal in a task plan."""

    description: str = Field(
        ..., description="What the agent should accomplish in this sub-goal"
    )
    success_criteria: str = Field(
        ...,
        description="Observable condition that indicates this sub-goal is met "
        "(e.g., 'the download page is visible', 'the form is submitted')",
    )
    status: str = Field(
        default="pending",
        description="Current status: pending, active, completed, failed",
    )


class TaskPlan(BaseModel):
    """Structured output from the planner LLM call."""

    reasoning: str = Field(
        ..., description="Brief reasoning about how to decompose this task"
    )
    sub_goals: list[SubGoal] = Field(
        ..., description="Ordered list of sub-goals to accomplish the task"
    )


class ReflectionResult(BaseModel):
    """Structured output from the reflector LLM call."""

    sub_goal_met: bool = Field(
        ..., description="Whether the current sub-goal's success criteria are satisfied"
    )
    reasoning: str = Field(
        ..., description="Brief explanation of why the sub-goal is or isn't met"
    )
    should_replan: bool = Field(
        default=False,
        description="Whether the remaining plan should be regenerated due to "
        "unexpected state or repeated failures",
    )

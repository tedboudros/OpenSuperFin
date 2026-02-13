"""Task model -- scheduled task definitions read by the scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class Task(BaseModel):
    """A scheduled task stored as a JSON file in tasks/.

    The scheduler reads these files and fires tasks whose schedule matches.
    The AI can create tasks by writing new JSON files.
    """

    id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:12]}")
    name: str
    type: Literal["one_off", "recurring", "research", "comparison"] = "recurring"

    # Schedule (one of these must be set)
    cron_expression: str | None = None
    run_at: datetime | None = None

    # Execution
    handler: str  # registered TaskHandler name, e.g. "monitoring.check_exit"
    params: dict = Field(default_factory=dict)

    # Metadata
    enabled: bool = True
    created_by: Literal["human", "ai"] = "human"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parent_task_id: str | None = None

    # Execution history
    last_run_at: datetime | None = None
    last_result: str | None = None
    run_count: int = 0


class TaskResult(BaseModel):
    """Result returned by a TaskHandler after execution."""

    status: Literal["success", "error", "no_action"] = "success"
    message: str = ""
    created_tasks: list[str] = Field(default_factory=list)  # IDs of any child tasks created

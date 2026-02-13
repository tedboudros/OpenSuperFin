"""Event model -- the universal message format for inter-component communication."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class Event(BaseModel):
    """A typed event that flows through the EventBus.

    Every inter-component communication is an Event. Events are persisted
    to daily JSONL files for auditability.
    """

    id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    source: str
    payload: dict = Field(default_factory=dict)
    metadata: dict | None = None

    def derive(self, type: str, source: str, payload: dict | None = None) -> Event:
        """Create a new event in the same correlation chain."""
        return Event(
            type=type,
            correlation_id=self.correlation_id,
            source=source,
            payload=payload or {},
        )


# -- Event type constants --

class EventTypes:
    """Well-known event type strings."""

    # Integration
    INTEGRATION_INPUT = "integration.input"
    INTEGRATION_OUTPUT = "integration.output"

    # Scheduler
    SCHEDULE_FIRED = "schedule.fired"

    # AI Engine
    CONTEXT_ASSEMBLED = "context.assembled"
    MEMO_CREATED = "memo.created"
    SIGNAL_PROPOSED = "signal.proposed"

    # Risk Engine
    SIGNAL_APPROVED = "signal.approved"
    SIGNAL_REJECTED = "signal.rejected"

    # Delivery
    SIGNAL_DELIVERED = "signal.delivered"

    # Position lifecycle
    POSITION_CONFIRMED = "position.confirmed"
    POSITION_SKIPPED = "position.skipped"
    POSITION_UPDATED = "position.updated"

    # Tasks
    TASK_CREATED = "task.created"

    # Learning
    MEMORY_CREATED = "memory.created"

    # Alerts
    ALERT_TRIGGERED = "alert.triggered"

    # Simulation
    SIMULATION_STARTED = "simulation.started"
    SIMULATION_COMPLETED = "simulation.completed"

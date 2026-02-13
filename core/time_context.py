"""TimeContext -- controls what data the system can see.

In production mode, current_time is always real 'now'.
In simulation mode, current_time is a historical date, preventing lookahead bias.
All data queries pass through TimeContext for filtering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class TimeContext(BaseModel):
    """Controls temporal visibility for the entire system."""

    current_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: Literal["production", "simulation"] = "production"
    simulation_id: str | None = None

    @classmethod
    def now(cls) -> TimeContext:
        """Create a production-mode TimeContext with real current time."""
        return cls(
            current_time=datetime.now(timezone.utc),
            mode="production",
        )

    @classmethod
    def at(cls, dt: datetime, simulation_id: str) -> TimeContext:
        """Create a simulation-mode TimeContext at a specific historical date."""
        return cls(
            current_time=dt,
            mode="simulation",
            simulation_id=simulation_id,
        )

    def advance_to(self, dt: datetime) -> None:
        """Advance the simulated time (only valid in simulation mode)."""
        if self.mode != "simulation":
            raise RuntimeError("Cannot advance time in production mode")
        self.current_time = dt

    @property
    def is_simulation(self) -> bool:
        return self.mode == "simulation"

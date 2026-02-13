"""Signal and Position models -- trade recommendations and tracked positions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class Signal(BaseModel):
    """A trade recommendation produced by the AI and gated by the Risk Engine."""

    id: str = Field(default_factory=lambda: f"sig_{uuid4().hex[:12]}")
    ticker: str
    direction: Literal["buy", "sell", "hold"]
    catalyst: str
    confidence: float = Field(ge=0.0, le=1.0)
    entry_target: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    horizon: str = ""
    memo_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""

    # Risk engine fields (populated after gate)
    status: Literal["proposed", "approved", "rejected", "delivered"] = "proposed"
    risk_result: RiskResult | None = None
    delivered_at: datetime | None = None
    delivered_via: str | None = None


class Position(BaseModel):
    """A tracked position in either the AI or human portfolio."""

    ticker: str
    direction: Literal["long", "short"]
    size: float | None = None
    entry_price: float
    current_price: float | None = None
    pnl: float | None = None
    pnl_percent: float | None = None

    # Lifecycle
    status: Literal[
        "signaled",
        "confirmed",
        "assumed",
        "skipped",
        "monitoring",
        "exit_signaled",
        "closed",
    ] = "signaled"

    # Tracking
    portfolio: Literal["ai", "human"]
    signal_id: str | None = None
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    close_price: float | None = None
    realized_pnl: float | None = None
    realized_pnl_percent: float | None = None

    # Human portfolio only
    confirmed_at: datetime | None = None
    confirmed_via: str | None = None
    user_notes: str | None = None

    def update_pnl(self, current_price: float) -> None:
        """Recalculate unrealized P&L from current price."""
        self.current_price = current_price
        if self.direction == "long":
            self.pnl = (current_price - self.entry_price) * (self.size or 1)
            self.pnl_percent = ((current_price - self.entry_price) / self.entry_price) * 100
        else:
            self.pnl = (self.entry_price - current_price) * (self.size or 1)
            self.pnl_percent = ((self.entry_price - current_price) / self.entry_price) * 100


class RuleEvaluation(BaseModel):
    """Result of a single risk rule evaluation."""

    rule_name: str
    passed: bool
    reason: str
    current_value: float | None = None
    limit_value: float | None = None


class RiskResult(BaseModel):
    """Aggregate result of all risk rule evaluations."""

    approved: bool
    evaluations: list[RuleEvaluation] = Field(default_factory=list)

    @property
    def failed_rules(self) -> list[RuleEvaluation]:
        return [e for e in self.evaluations if not e.passed]

    @property
    def summary(self) -> str:
        if self.approved:
            return f"Approved ({len(self.evaluations)} rules passed)"
        failed = ", ".join(e.rule_name for e in self.failed_rules)
        return f"Rejected (failed: {failed})"


# Rebuild Signal to resolve forward ref to RiskResult
Signal.model_rebuild()

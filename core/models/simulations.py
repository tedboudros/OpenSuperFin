"""Simulation models -- backtest configuration and performance metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class SimulationConfig(BaseModel):
    """Configuration for a simulation run."""

    date_range: tuple[str, str]  # (start, end) as YYYY-MM-DD strings
    initial_capital: float = 100_000.0
    ai_provider: str = ""
    ai_model: str = ""
    agents: list[str] = Field(default_factory=list)
    risk_config: dict = Field(default_factory=dict)
    slippage_bps: float = 10.0
    commission_per_trade: float = 1.0


class PerformanceMetrics(BaseModel):
    """Standard performance metrics for a simulation or live portfolio."""

    # Returns
    total_return: float = 0.0
    cagr: float = 0.0
    sharpe_ratio: float = 0.0

    # Risk
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    volatility: float = 0.0

    # Quality
    hit_rate: float = 0.0
    win_loss_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_signals: int = 0
    total_trades: int = 0

    # Benchmarks
    vs_spy: float = 0.0
    vs_qqq: float = 0.0


class SimulationRun(BaseModel):
    """A record of a backtest / simulation run."""

    id: str = Field(default_factory=lambda: f"sim_{uuid4().hex[:12]}")
    name: str
    config: SimulationConfig
    status: Literal["pending", "running", "completed", "failed"] = "pending"

    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = None
    signal_count: int = 0
    metrics: PerformanceMetrics | None = None
    error: str | None = None

    def mark_started(self) -> None:
        self.status = "running"
        self.started_at = datetime.now(timezone.utc)

    def mark_completed(self, metrics: PerformanceMetrics, signal_count: int) -> None:
        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc)
        self.metrics = metrics
        self.signal_count = signal_count
        if self.started_at:
            self.elapsed_seconds = (self.completed_at - self.started_at).total_seconds()

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.completed_at = datetime.now(timezone.utc)
        self.error = error
        if self.started_at:
            self.elapsed_seconds = (self.completed_at - self.started_at).total_seconds()

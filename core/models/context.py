"""ContextPack model -- structured input assembled for the AI orchestrator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.models.events import Event
from core.models.market import MarketSnapshot, MarketRegime
from core.models.memories import Memory
from core.models.signals import Position
from core.time_context import TimeContext


class PortfolioSummary(BaseModel):
    """Summarized view of a portfolio (AI or human)."""

    portfolio_type: Literal["ai", "human"]
    total_value: float = 0.0
    cash: float = 0.0
    positions: list[Position] = Field(default_factory=list)
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    sector_exposure: dict[str, float] = Field(default_factory=dict)


class ContextPack(BaseModel):
    """Everything the AI needs to make a decision.

    Assembled by the orchestrator before running the agent chain.
    This is an in-memory object, not persisted to disk.
    """

    time_context: TimeContext
    market_snapshot: MarketSnapshot = Field(default_factory=MarketSnapshot)
    regime: MarketRegime = Field(default_factory=MarketRegime)

    ai_portfolio: PortfolioSummary = Field(
        default_factory=lambda: PortfolioSummary(portfolio_type="ai")
    )
    human_portfolio: PortfolioSummary = Field(
        default_factory=lambda: PortfolioSummary(portfolio_type="human")
    )

    trigger_event: Event | None = None
    recent_events: list[Event] = Field(default_factory=list)
    relevant_memories: list[Memory] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)

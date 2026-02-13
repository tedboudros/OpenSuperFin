"""Market data models -- price data and market state snapshots."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class MarketData(BaseModel):
    """A single market data point stored in SQLite.

    The `available_at` field is critical for simulation integrity:
    it marks when this data became available to the system, preventing
    lookahead bias in backtests.
    """

    ticker: str
    timestamp: datetime
    available_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None
    source: str = ""
    data_type: str = "price"
    metadata: dict | None = None


class MarketSnapshot(BaseModel):
    """A point-in-time view of market state, used in context packs."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    prices: dict[str, float] = Field(default_factory=dict)
    vix: float | None = None
    yields: dict[str, float] = Field(default_factory=dict)
    dxy: float | None = None
    sector_performance: dict[str, float] | None = None


class MarketRegime(BaseModel):
    """Current market regime assessment."""

    label: str = "unknown"
    confidence: float = 0.0
    indicators: dict[str, float] = Field(default_factory=dict)

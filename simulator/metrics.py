"""Performance metrics -- pure Python math, no numpy/pandas required.

Calculates standard trading performance metrics from a list of trades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Trade:
    """A completed trade for metrics calculation."""

    ticker: str
    direction: str  # "long" or "short"
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_percent: float
    holding_days: int


def calculate_metrics(
    trades: list[Trade],
    initial_capital: float = 100_000.0,
    trading_days: int = 252,
    risk_free_rate: float = 0.04,
) -> dict:
    """Calculate performance metrics from a list of completed trades.

    Returns a dict matching the PerformanceMetrics model fields.
    """
    if not trades:
        return _empty_metrics()

    # Basic counts
    total_trades = len(trades)
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]

    # P&L
    total_pnl = sum(t.pnl for t in trades)
    total_return = total_pnl / initial_capital

    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))

    # Hit rate
    hit_rate = len(winners) / total_trades if total_trades else 0

    # Win/loss ratio
    avg_win = gross_profit / len(winners) if winners else 0
    avg_loss = gross_loss / len(losers) if losers else 0
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # Profit factor
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Build equity curve for Sharpe, drawdown, etc.
    equity_curve = _build_equity_curve(trades, initial_capital)

    # CAGR
    total_days = sum(t.holding_days for t in trades)
    years = max(total_days / 365.25, 0.01)
    final_equity = initial_capital + total_pnl
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if years > 0 else 0

    # Daily returns for Sharpe
    daily_returns = _daily_returns(equity_curve)
    sharpe = _sharpe_ratio(daily_returns, risk_free_rate, trading_days)

    # Volatility
    volatility = _stdev(daily_returns) * math.sqrt(trading_days) if daily_returns else 0

    # Max drawdown
    max_dd, max_dd_days = _max_drawdown(equity_curve)

    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "max_drawdown_duration_days": max_dd_days,
        "volatility": volatility,
        "hit_rate": hit_rate,
        "win_loss_ratio": win_loss_ratio,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "total_signals": total_trades,
        "total_trades": total_trades,
        "vs_spy": 0.0,  # filled in by the simulator with benchmark data
        "vs_qqq": 0.0,
    }


def _empty_metrics() -> dict:
    """Return zeroed-out metrics."""
    return {
        "total_return": 0.0, "cagr": 0.0, "sharpe_ratio": 0.0,
        "max_drawdown": 0.0, "max_drawdown_duration_days": 0,
        "volatility": 0.0, "hit_rate": 0.0, "win_loss_ratio": 0.0,
        "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "total_signals": 0, "total_trades": 0, "vs_spy": 0.0, "vs_qqq": 0.0,
    }


def _build_equity_curve(trades: list[Trade], initial_capital: float) -> list[float]:
    """Build a simple equity curve from sequential trades."""
    curve = [initial_capital]
    equity = initial_capital
    for t in trades:
        equity += t.pnl
        curve.append(equity)
    return curve


def _daily_returns(equity_curve: list[float]) -> list[float]:
    """Approximate daily returns from the equity curve."""
    if len(equity_curve) < 2:
        return []
    return [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]


def _sharpe_ratio(
    daily_returns: list[float],
    risk_free_rate: float = 0.04,
    trading_days: int = 252,
) -> float:
    """Calculate annualized Sharpe ratio."""
    if not daily_returns:
        return 0.0

    daily_rf = risk_free_rate / trading_days
    excess = [r - daily_rf for r in daily_returns]

    avg = sum(excess) / len(excess)
    std = _stdev(excess)

    if std == 0:
        return 0.0

    return (avg / std) * math.sqrt(trading_days)


def _max_drawdown(equity_curve: list[float]) -> tuple[float, int]:
    """Calculate maximum drawdown and its duration in periods.

    Returns (max_drawdown_fraction, max_drawdown_duration).
    """
    if not equity_curve:
        return 0.0, 0

    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_duration = 0
    current_dd_start = 0
    in_drawdown = False

    for i, value in enumerate(equity_curve):
        if value > peak:
            peak = value
            if in_drawdown:
                duration = i - current_dd_start
                max_dd_duration = max(max_dd_duration, duration)
            in_drawdown = False
        else:
            dd = (peak - value) / peak if peak > 0 else 0
            if dd > 0 and not in_drawdown:
                current_dd_start = i
                in_drawdown = True
            max_dd = max(max_dd, dd)

    # Handle ongoing drawdown
    if in_drawdown:
        duration = len(equity_curve) - current_dd_start
        max_dd_duration = max(max_dd_duration, duration)

    return max_dd, max_dd_duration


def _stdev(values: list[float]) -> float:
    """Calculate standard deviation (population)."""
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    variance = sum((x - avg) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)

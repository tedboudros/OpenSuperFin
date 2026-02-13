"""Dual portfolio tracker -- reads position files for both AI and human portfolios."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.data.store import Store
from core.models.context import PortfolioSummary
from core.models.signals import Position, Signal

logger = logging.getLogger(__name__)


class PortfolioTracker:
    """Manages dual portfolio state (AI paper + human actual).

    Reads/writes position JSON files in positions/ai/ and positions/human/.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Read portfolio state
    # ------------------------------------------------------------------

    def get_summary(self, portfolio_type: str) -> PortfolioSummary:
        """Build a PortfolioSummary from position files."""
        subdir = f"positions/{portfolio_type}"
        positions = self._store.list_json(subdir, Position)

        open_positions = [p for p in positions if p.status not in ("closed", "skipped")]

        total_pnl = sum(p.pnl or 0 for p in open_positions)
        total_value = sum(
            (p.current_price or p.entry_price) * (p.size or 1) for p in open_positions
        )

        return PortfolioSummary(
            portfolio_type=portfolio_type,  # type: ignore[arg-type]
            total_value=total_value,
            positions=open_positions,
            total_pnl=total_pnl,
            total_pnl_percent=(total_pnl / total_value * 100) if total_value else 0,
        )

    def get_position(self, portfolio_type: str, ticker: str) -> Position | None:
        """Get a specific position by ticker."""
        return self._store.read_json(
            f"positions/{portfolio_type}",
            f"{ticker}.json",
            Position,
        )

    def list_positions(self, portfolio_type: str) -> list[Position]:
        """List all positions in a portfolio."""
        return self._store.list_json(f"positions/{portfolio_type}", Position)

    # ------------------------------------------------------------------
    # AI portfolio operations (always executes signals)
    # ------------------------------------------------------------------

    def ai_open_position(self, signal: Signal) -> Position:
        """Open a position in the AI portfolio (always executed)."""
        position = Position(
            ticker=signal.ticker,
            direction="long" if signal.direction == "buy" else "short",
            entry_price=signal.entry_target or 0,
            status="monitoring",
            portfolio="ai",
            signal_id=signal.id,
        )
        self._store.write_json("positions/ai", f"{signal.ticker}.json", position)
        logger.info("AI portfolio: opened %s %s at %.2f", position.direction, signal.ticker, position.entry_price)
        return position

    def ai_close_position(self, ticker: str, close_price: float) -> Position | None:
        """Close a position in the AI portfolio."""
        position = self.get_position("ai", ticker)
        if not position:
            return None

        position.status = "closed"
        position.close_price = close_price
        position.closed_at = datetime.now(timezone.utc)

        if position.direction == "long":
            position.realized_pnl = (close_price - position.entry_price) * (position.size or 1)
        else:
            position.realized_pnl = (position.entry_price - close_price) * (position.size or 1)

        if position.entry_price:
            position.realized_pnl_percent = (
                (position.realized_pnl or 0) / (position.entry_price * (position.size or 1)) * 100
            )

        self._store.write_json("positions/ai", f"{ticker}.json", position)
        logger.info("AI portfolio: closed %s at %.2f (P&L: %.2f)", ticker, close_price, position.realized_pnl or 0)
        return position

    # ------------------------------------------------------------------
    # Human portfolio operations (user-driven)
    # ------------------------------------------------------------------

    def human_confirm_position(
        self,
        signal: Signal,
        entry_price: float,
        size: float | None = None,
        via: str = "unknown",
        notes: str | None = None,
    ) -> Position:
        """User confirmed they took a trade."""
        position = Position(
            ticker=signal.ticker,
            direction="long" if signal.direction == "buy" else "short",
            size=size,
            entry_price=entry_price,
            status="confirmed",
            portfolio="human",
            signal_id=signal.id,
            confirmed_at=datetime.now(timezone.utc),
            confirmed_via=via,
            user_notes=notes,
        )
        self._store.write_json("positions/human", f"{signal.ticker}.json", position)
        logger.info("Human portfolio: confirmed %s at %.2f via %s", signal.ticker, entry_price, via)
        return position

    def human_skip_position(
        self,
        signal: Signal,
        via: str = "unknown",
        notes: str | None = None,
    ) -> Position:
        """User explicitly skipped a signal."""
        position = Position(
            ticker=signal.ticker,
            direction="long" if signal.direction == "buy" else "short",
            entry_price=signal.entry_target or 0,
            status="skipped",
            portfolio="human",
            signal_id=signal.id,
            confirmed_at=datetime.now(timezone.utc),
            confirmed_via=via,
            user_notes=notes,
        )
        self._store.write_json("positions/human", f"{signal.ticker}.json", position)
        logger.info("Human portfolio: skipped %s (%s)", signal.ticker, notes or "no reason")
        return position

    def human_close_position(self, ticker: str, close_price: float, via: str = "unknown") -> Position | None:
        """User reported closing a position."""
        position = self.get_position("human", ticker)
        if not position:
            return None

        position.status = "closed"
        position.close_price = close_price
        position.closed_at = datetime.now(timezone.utc)
        position.confirmed_via = via

        if position.direction == "long":
            position.realized_pnl = (close_price - position.entry_price) * (position.size or 1)
        else:
            position.realized_pnl = (position.entry_price - close_price) * (position.size or 1)

        if position.entry_price:
            position.realized_pnl_percent = (
                (position.realized_pnl or 0) / (position.entry_price * (position.size or 1)) * 100
            )

        self._store.write_json("positions/human", f"{ticker}.json", position)
        logger.info("Human portfolio: closed %s at %.2f via %s", ticker, close_price, via)
        return position

    def human_initiated_trade(
        self,
        ticker: str,
        direction: str,
        entry_price: float,
        size: float | None = None,
        via: str = "unknown",
        notes: str | None = None,
    ) -> Position:
        """User took a trade the AI didn't suggest."""
        position = Position(
            ticker=ticker,
            direction=direction,  # type: ignore[arg-type]
            size=size,
            entry_price=entry_price,
            status="confirmed",
            portfolio="human",
            signal_id=None,  # no AI signal
            confirmed_at=datetime.now(timezone.utc),
            confirmed_via=via,
            user_notes=notes,
        )
        self._store.write_json("positions/human", f"{ticker}.json", position)
        logger.info("Human portfolio: initiated %s %s at %.2f via %s", direction, ticker, entry_price, via)
        return position

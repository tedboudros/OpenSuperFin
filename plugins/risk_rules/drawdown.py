"""Drawdown rule -- pause signals if portfolio drawdown exceeds threshold."""

from __future__ import annotations

from core.models.context import PortfolioSummary
from core.models.signals import RuleEvaluation, Signal

PLUGIN_META = {
    "name": "drawdown",
    "display_name": "Portfolio Drawdown",
    "description": "Pause signals if portfolio drawdown exceeds threshold",
    "category": "risk_rule",
    "protocols": ["risk_rule"],
    "class_name": "DrawdownRule",
    "pip_dependencies": [],
    "setup_instructions": "Automatically pauses new signals when the portfolio drops too much.",
    "config_fields": [
        {
            "key": "max_portfolio_drawdown",
            "label": "Max drawdown (%)",
            "type": "number",
            "required": False,
            "default": 15,
            "description": "Maximum portfolio drawdown before pausing signals",
            "placeholder": "15",
        },
    ],
}


class DrawdownRule:
    """Reject signals if the portfolio has drawn down too much."""

    def __init__(self, max_portfolio_drawdown: float = 0.15) -> None:
        self.max_portfolio_drawdown = max_portfolio_drawdown

    @property
    def name(self) -> str:
        return "drawdown"

    def evaluate(self, signal: Signal, portfolio: PortfolioSummary) -> RuleEvaluation:
        if not portfolio.positions or portfolio.total_value <= 0:
            return RuleEvaluation(
                rule_name=self.name,
                passed=True,
                reason="No positions -- drawdown check passes",
            )

        # Calculate current drawdown from total P&L
        drawdown = 0.0
        if portfolio.total_pnl < 0 and portfolio.total_value > 0:
            # Approximate drawdown as negative P&L relative to portfolio value + losses
            peak_estimate = portfolio.total_value - portfolio.total_pnl
            drawdown = abs(portfolio.total_pnl) / peak_estimate if peak_estimate > 0 else 0

        passed = drawdown < self.max_portfolio_drawdown
        return RuleEvaluation(
            rule_name=self.name,
            passed=passed,
            reason=(
                f"Portfolio drawdown {drawdown:.1%} within limit {self.max_portfolio_drawdown:.1%}"
                if passed
                else f"Portfolio drawdown {drawdown:.1%} exceeds limit {self.max_portfolio_drawdown:.1%}"
            ),
            current_value=drawdown,
            limit_value=self.max_portfolio_drawdown,
        )

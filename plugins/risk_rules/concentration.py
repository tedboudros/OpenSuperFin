"""Concentration rule -- limit exposure to any single position or sector."""

from __future__ import annotations

from core.models.context import PortfolioSummary
from core.models.signals import RuleEvaluation, Signal

PLUGIN_META = {
    "name": "concentration",
    "display_name": "Position Concentration",
    "description": "Limit exposure to any single position or sector",
    "category": "risk_rule",
    "protocols": ["risk_rule"],
    "class_name": "ConcentrationRule",
    "pip_dependencies": [],
    "setup_instructions": "Prevents over-concentration in a single position or sector.",
    "config_fields": [
        {
            "key": "max_single_position",
            "label": "Max single position (%)",
            "type": "number",
            "required": False,
            "default": 15,
            "description": "Maximum portfolio percentage in one position",
            "placeholder": "15",
        },
        {
            "key": "max_sector_exposure",
            "label": "Max sector exposure (%)",
            "type": "number",
            "required": False,
            "default": 30,
            "description": "Maximum portfolio percentage in one sector",
            "placeholder": "30",
        },
    ],
}


class ConcentrationRule:
    """Reject signals that would create excessive concentration."""

    def __init__(
        self,
        max_single_position: float = 0.15,
        max_sector_exposure: float = 0.30,
    ) -> None:
        self.max_single_position = max_single_position
        self.max_sector_exposure = max_sector_exposure

    @property
    def name(self) -> str:
        return "concentration"

    def evaluate(self, signal: Signal, portfolio: PortfolioSummary) -> RuleEvaluation:
        if not portfolio.positions or portfolio.total_value <= 0:
            return RuleEvaluation(
                rule_name=self.name,
                passed=True,
                reason="No existing positions -- concentration check passes",
            )

        # Check if ticker already exists in portfolio
        existing = [p for p in portfolio.positions if p.ticker == signal.ticker and p.status not in ("closed", "skipped")]
        if existing:
            position_value = sum(
                (p.current_price or p.entry_price) * (p.size or 1) for p in existing
            )
            position_pct = position_value / portfolio.total_value if portfolio.total_value else 0

            if position_pct >= self.max_single_position:
                return RuleEvaluation(
                    rule_name=self.name,
                    passed=False,
                    reason=(
                        f"{signal.ticker} already {position_pct:.1%} of portfolio "
                        f"(limit: {self.max_single_position:.1%})"
                    ),
                    current_value=position_pct,
                    limit_value=self.max_single_position,
                )

        return RuleEvaluation(
            rule_name=self.name,
            passed=True,
            reason="Position concentration within limits",
        )

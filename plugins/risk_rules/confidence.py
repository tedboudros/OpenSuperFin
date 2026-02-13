"""Confidence rule -- reject signals below a minimum confidence threshold."""

from __future__ import annotations

from core.models.context import PortfolioSummary
from core.models.signals import RuleEvaluation, Signal

PLUGIN_META = {
    "name": "confidence",
    "display_name": "Confidence Threshold",
    "description": "Reject signals below a minimum confidence level",
    "category": "risk_rule",
    "protocols": ["risk_rule"],
    "class_name": "ConfidenceRule",
    "pip_dependencies": [],
    "setup_instructions": "Signals with confidence below this threshold are automatically rejected.",
    "config_fields": [
        {
            "key": "min_confidence",
            "label": "Minimum confidence",
            "type": "number",
            "required": False,
            "default": 0.6,
            "description": "Minimum signal confidence (0.0 to 1.0)",
            "placeholder": "0.6",
        },
    ],
}


class ConfidenceRule:
    """Reject signals with confidence below the configured minimum."""

    def __init__(self, min_confidence: float = 0.6) -> None:
        self.min_confidence = min_confidence

    @property
    def name(self) -> str:
        return "confidence"

    def evaluate(self, signal: Signal, portfolio: PortfolioSummary) -> RuleEvaluation:
        passed = signal.confidence >= self.min_confidence
        return RuleEvaluation(
            rule_name=self.name,
            passed=passed,
            reason=(
                f"Confidence {signal.confidence:.2f} meets minimum {self.min_confidence:.2f}"
                if passed
                else f"Confidence {signal.confidence:.2f} below minimum {self.min_confidence:.2f}"
            ),
            current_value=signal.confidence,
            limit_value=self.min_confidence,
        )

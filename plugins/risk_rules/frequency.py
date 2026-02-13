"""Frequency rule -- limit the number of signals per day."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.models.context import PortfolioSummary
from core.models.signals import RuleEvaluation, Signal

PLUGIN_META = {
    "name": "frequency",
    "display_name": "Signal Frequency",
    "description": "Limit the number of approved signals per day",
    "category": "risk_rule",
    "protocols": ["risk_rule"],
    "class_name": "FrequencyRule",
    "pip_dependencies": [],
    "setup_instructions": "Prevents signal spam by capping daily approved signals.",
    "config_fields": [
        {
            "key": "max_signals_per_day",
            "label": "Max signals per day",
            "type": "number",
            "required": False,
            "default": 5,
            "description": "Maximum number of approved signals per day",
            "placeholder": "5",
        },
    ],
}


class FrequencyRule:
    """Reject signals if too many have already been approved today."""

    def __init__(self, max_signals_per_day: int = 5, events_dir: Path | None = None) -> None:
        self.max_signals_per_day = max_signals_per_day
        self._events_dir = events_dir

    @property
    def name(self) -> str:
        return "frequency"

    def evaluate(self, signal: Signal, portfolio: PortfolioSummary) -> RuleEvaluation:
        today_count = self._count_todays_signals()

        passed = today_count < self.max_signals_per_day
        return RuleEvaluation(
            rule_name=self.name,
            passed=passed,
            reason=(
                f"{today_count} signals today (limit: {self.max_signals_per_day})"
                if passed
                else f"Already {today_count} signals today (limit: {self.max_signals_per_day})"
            ),
            current_value=float(today_count),
            limit_value=float(self.max_signals_per_day),
        )

    def _count_todays_signals(self) -> int:
        """Count how many signal.approved events occurred today."""
        if not self._events_dir:
            return 0

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self._events_dir / f"{today}.jsonl"

        if not filepath.exists():
            return 0

        count = 0
        try:
            with open(filepath) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("type") == "signal.approved":
                            count += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

        return count

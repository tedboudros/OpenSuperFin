"""Risk engine -- subscribes to signal.proposed, validates, publishes approved/rejected.

The Risk Engine is the ONLY component that can approve or reject signals.
The AI can retry with modified parameters but cannot override.
Completely deterministic. Zero LLM involvement.
"""

from __future__ import annotations

import logging

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.events import Event, EventTypes
from core.models.signals import RiskResult, RuleEvaluation, Signal
from core.registry import PluginRegistry
from risk.portfolio import PortfolioTracker

logger = logging.getLogger(__name__)


class RiskEngine:
    """Deterministic risk gate for trade signals.

    Subscribes to signal.proposed events, evaluates all registered
    RiskRule plugins, and publishes signal.approved or signal.rejected.
    """

    def __init__(
        self,
        bus: AsyncIOBus,
        store: Store,
        registry: PluginRegistry,
        portfolio: PortfolioTracker,
    ) -> None:
        self._bus = bus
        self._store = store
        self._registry = registry
        self._portfolio = portfolio

        # Subscribe to signal proposals
        bus.subscribe(EventTypes.SIGNAL_PROPOSED, self._handle_signal)

    async def _handle_signal(self, event: Event) -> None:
        """Evaluate a proposed signal against all risk rules."""
        try:
            signal = Signal(**event.payload)
        except Exception:
            logger.exception("Failed to parse signal from event payload")
            return

        # Get AI portfolio state for evaluation
        portfolio_summary = self._portfolio.get_summary("ai")

        # Run all registered risk rules
        rules = self._registry.get_all("risk_rule")
        evaluations: list[RuleEvaluation] = []

        for rule in rules:
            try:
                evaluation = rule.evaluate(signal, portfolio_summary)
                evaluations.append(evaluation)
            except Exception:
                logger.exception("Risk rule '%s' raised an error", rule.name)
                evaluations.append(RuleEvaluation(
                    rule_name=rule.name,
                    passed=False,
                    reason=f"Rule raised an exception",
                ))

        # Determine overall result
        all_passed = all(e.passed for e in evaluations)
        result = RiskResult(approved=all_passed, evaluations=evaluations)

        if all_passed:
            # APPROVED -- open position in AI portfolio, publish approved event
            signal.status = "approved"
            signal.risk_result = result
            signal.delivery_errors = None

            self._portfolio.ai_open_position(signal)
            self._store.write_json("signals", f"{signal.id}.json", signal)

            approved_event = event.derive(
                type=EventTypes.SIGNAL_APPROVED,
                source="risk_engine",
                payload=signal.model_dump(mode="json"),
            )
            await self._bus.publish(approved_event)

            logger.info(
                "Signal APPROVED: %s %s (confidence=%.2f, %d rules passed)",
                signal.direction.upper(),
                signal.ticker,
                signal.confidence,
                len(evaluations),
            )
        else:
            # REJECTED
            signal.status = "rejected"
            signal.risk_result = result
            self._store.write_json("signals", f"{signal.id}.json", signal)

            rejected_event = event.derive(
                type=EventTypes.SIGNAL_REJECTED,
                source="risk_engine",
                payload=signal.model_dump(mode="json"),
            )
            await self._bus.publish(rejected_event)

            failed_names = [e.rule_name for e in evaluations if not e.passed]
            logger.info(
                "Signal REJECTED: %s %s (failed: %s)",
                signal.direction.upper(),
                signal.ticker,
                ", ".join(failed_names),
            )

    async def evaluate_signal(self, signal: Signal) -> RiskResult:
        """Evaluate a signal without publishing events (for testing/preview)."""
        portfolio_summary = self._portfolio.get_summary("ai")
        rules = self._registry.get_all("risk_rule")
        evaluations = []

        for rule in rules:
            try:
                evaluations.append(rule.evaluate(signal, portfolio_summary))
            except Exception:
                evaluations.append(RuleEvaluation(
                    rule_name=rule.name,
                    passed=False,
                    reason="Rule raised an exception",
                ))

        return RiskResult(
            approved=all(e.passed for e in evaluations),
            evaluations=evaluations,
        )

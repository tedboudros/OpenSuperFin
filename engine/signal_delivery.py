"""Signal delivery service.

Subscribes to approved signals, delivers via output adapters, and updates
signal lifecycle state to delivered when any adapter succeeds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.events import Event, EventTypes
from core.models.signals import Signal
from core.registry import PluginRegistry

logger = logging.getLogger(__name__)


class SignalDeliveryService:
    """Deliver approved signals through all configured output adapters."""

    def __init__(
        self,
        bus: AsyncIOBus,
        store: Store,
        registry: PluginRegistry,
        confirmation_timeout: timedelta,
    ) -> None:
        self._bus = bus
        self._store = store
        self._registry = registry
        self._confirmation_timeout = confirmation_timeout
        self._bus.subscribe(EventTypes.SIGNAL_APPROVED, self._handle_signal_approved)

    async def _handle_signal_approved(self, event: Event) -> None:
        try:
            signal = Signal(**event.payload)
        except Exception:
            logger.exception("Failed to parse approved signal event payload")
            return

        adapters = self._registry.get_all("output")
        successes: list[str] = []
        errors: list[str] = []

        for adapter in adapters:
            try:
                result = await adapter.send(signal, memo=None)
            except Exception as exc:
                logger.exception(
                    "Signal delivery failed via %s for %s",
                    getattr(adapter, "name", "unknown"),
                    signal.id,
                )
                errors.append(f"{getattr(adapter, 'name', 'unknown')}: {exc}")
                continue

            if result.success:
                successes.append(result.adapter or getattr(adapter, "name", "unknown"))
            else:
                message = result.message or "delivery failed"
                errors.append(f"{result.adapter or getattr(adapter, 'name', 'unknown')}: {message}")

        if successes:
            delivered_at = datetime.now(timezone.utc)
            signal.status = "delivered"
            signal.delivered_at = delivered_at
            signal.delivered_via = ", ".join(sorted(set(successes)))
            signal.confirmation_status = "pending"
            signal.confirmation_due_at = delivered_at + self._confirmation_timeout
            signal.confirmation_reminder_sent_at = None
            signal.delivery_errors = errors or None
            self._store.write_json("signals", f"{signal.id}.json", signal)

            delivered_event = event.derive(
                type=EventTypes.SIGNAL_DELIVERED,
                source="signal_delivery",
                payload=signal.model_dump(mode="json"),
            )
            await self._bus.publish(delivered_event)
            return

        signal.status = "approved"
        signal.delivery_errors = errors or ["No output adapters configured"]
        self._store.write_json("signals", f"{signal.id}.json", signal)

        alert_event = event.derive(
            type=EventTypes.ALERT_TRIGGERED,
            source="signal_delivery",
            payload={
                "level": "error",
                "signal_id": signal.id,
                "ticker": signal.ticker,
                "message": "Signal approved but delivery failed on all output adapters",
                "errors": signal.delivery_errors,
            },
        )
        await self._bus.publish(alert_event)

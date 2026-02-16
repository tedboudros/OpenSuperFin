"""Pending confirmation reminder watcher."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.events import Event, EventTypes
from core.models.signals import Signal

logger = logging.getLogger(__name__)


class PendingConfirmationWatcher:
    """Send one reminder when pending signal confirmations become overdue."""

    def __init__(
        self,
        bus: AsyncIOBus,
        store: Store,
        check_interval_seconds: int = 60,
    ) -> None:
        self._bus = bus
        self._store = store
        self._check_interval_seconds = max(1, int(check_interval_seconds))
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Pending confirmation watcher started (check every %ds)",
            self._check_interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Pending confirmation watcher stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._scan_once()
            except Exception:
                logger.exception("Pending confirmation scan failed")
            await asyncio.sleep(self._check_interval_seconds)

    async def _scan_once(self) -> None:
        now = datetime.now(timezone.utc)
        signals = self._store.list_json("signals", Signal)

        for signal in signals:
            if signal.status != "delivered":
                continue
            if signal.confirmation_status != "pending":
                continue
            if signal.confirmation_reminder_sent_at is not None:
                continue
            if signal.confirmation_due_at is None or signal.confirmation_due_at > now:
                continue

            reminder = (
                f"Signal confirmation pending: {signal.direction.upper()} {signal.ticker} "
                f"(signal_id={signal.id}). To confirm, provide entry price and quantity. "
                f"Example: confirm signal {signal.id} entry_price 123.45 quantity 10. "
                f"To skip: skip signal {signal.id} reason <optional reason>."
            )

            await self._bus.publish(Event(
                type=EventTypes.INTEGRATION_OUTPUT,
                source="pending_confirmation",
                payload={"text": reminder},
            ))

            signal.confirmation_reminder_sent_at = now
            self._store.write_json("signals", f"{signal.id}.json", signal)

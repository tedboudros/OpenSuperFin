"""AsyncIOBus -- default EventBus implementation using in-process async pub/sub.

Events are dispatched to subscribers via asyncio.create_task() and
persisted to daily JSONL files for audit.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from core.models.events import Event

logger = logging.getLogger(__name__)

Callback = Callable[[Event], Coroutine[Any, Any, None]]


class AsyncIOBus:
    """In-process async pub/sub event bus with JSONL audit logging.

    Implements the EventBus protocol.

    Usage:
        bus = AsyncIOBus(events_dir=Path("~/.opensuperfin/events"))
        bus.subscribe("signal.proposed", my_handler)
        await bus.publish(event)
    """

    def __init__(self, events_dir: Path) -> None:
        self._subscribers: dict[str, list[Callback]] = {}
        self._wildcard_subscribers: list[Callback] = []
        self._events_dir = events_dir
        self._events_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "asyncio_bus"

    async def publish(self, event: Event) -> None:
        """Publish an event: persist to audit log, then dispatch to subscribers."""
        # Persist to JSONL audit log
        self._persist(event)

        # Dispatch to type-specific subscribers
        callbacks = self._subscribers.get(event.type, [])

        # Also dispatch to wildcard subscribers (subscribed to "*")
        callbacks = callbacks + self._wildcard_subscribers

        if not callbacks:
            logger.debug("No subscribers for event type: %s", event.type)
            return

        logger.debug(
            "Publishing %s to %d subscriber(s) [correlation=%s]",
            event.type,
            len(callbacks),
            event.correlation_id,
        )

        # Fire all callbacks concurrently
        tasks = []
        for cb in callbacks:
            task = asyncio.create_task(self._safe_invoke(cb, event))
            tasks.append(task)

        # Wait for all to complete (don't let failures propagate)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def subscribe(self, event_type: str, callback: Callback) -> None:
        """Register a callback for events of the given type.

        Use event_type="*" to subscribe to all events.
        """
        if event_type == "*":
            self._wildcard_subscribers.append(callback)
        else:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
        logger.debug("Subscribed to '%s': %s", event_type, callback)

    def unsubscribe(self, event_type: str, callback: Callback) -> None:
        """Remove a previously registered callback."""
        if event_type == "*":
            if callback in self._wildcard_subscribers:
                self._wildcard_subscribers.remove(callback)
        elif event_type in self._subscribers:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    async def _safe_invoke(self, callback: Callback, event: Event) -> None:
        """Invoke a callback, catching and logging any exceptions."""
        try:
            await callback(event)
        except Exception:
            logger.exception(
                "Error in event handler for %s [correlation=%s]",
                event.type,
                event.correlation_id,
            )

    def _persist(self, event: Event) -> None:
        """Append event to today's JSONL audit file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self._events_dir / f"{today}.jsonl"

        line = event.model_dump_json() + "\n"

        try:
            with open(filepath, "a") as f:
                f.write(line)
        except OSError:
            logger.exception("Failed to persist event to %s", filepath)

    def subscriber_count(self, event_type: str | None = None) -> int:
        """Return the number of subscribers, optionally filtered by event type."""
        if event_type is None:
            total = sum(len(cbs) for cbs in self._subscribers.values())
            return total + len(self._wildcard_subscribers)
        if event_type == "*":
            return len(self._wildcard_subscribers)
        return len(self._subscribers.get(event_type, []))

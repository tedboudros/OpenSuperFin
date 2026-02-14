"""Notification task handler.

Sends a plain text message through configured output integrations.
"""

from __future__ import annotations

import logging

from core.bus import AsyncIOBus
from core.models.events import Event, EventTypes
from core.models.tasks import TaskResult

logger = logging.getLogger(__name__)


class NotificationsHandler:
    """Send scheduled notification messages to output adapters."""

    def __init__(self, bus: AsyncIOBus) -> None:
        self._bus = bus

    @property
    def name(self) -> str:
        return "notifications.send"

    async def run(self, params: dict) -> TaskResult:
        message = str(params.get("message", "")).strip()
        if not message:
            return TaskResult(
                status="error",
                message="Missing required param: message",
            )

        channel_id = params.get("channel_id")
        adapter = params.get("adapter")
        await self._bus.publish(Event(
            type=EventTypes.INTEGRATION_OUTPUT,
            source=self.name,
            payload={
                "text": message,
                "channel_id": channel_id,
                "adapter": adapter,
            },
        ))

        return TaskResult(
            status="success",
            message="Queued notification for delivery via integration.output",
        )

"""Generic integration output dispatcher.

Consumes integration.output events and delivers text through registered
output adapters that support send_text().
"""

from __future__ import annotations

import logging

from core.models.events import Event
from core.registry import PluginRegistry

logger = logging.getLogger(__name__)


class OutputDispatcher:
    """Deliver integration.output events via configured output adapters."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    async def handle_integration_output(self, event: Event) -> None:
        payload = event.payload or {}
        text = str(payload.get("text", "")).strip()
        if not text:
            logger.debug("integration.output ignored: missing text")
            return

        channel_id = payload.get("channel_id")
        adapter_name = payload.get("adapter")

        outputs = self._registry.get_all("output")
        delivered = 0

        for output in outputs:
            if adapter_name and getattr(output, "name", None) != adapter_name:
                continue

            send_text = getattr(output, "send_text", None)
            if send_text is None:
                continue

            try:
                await send_text(text, channel_id=channel_id)
                delivered += 1
            except Exception:
                logger.exception(
                    "Failed delivering integration.output via %s",
                    getattr(output, "name", "unknown"),
                )

        if delivered == 0:
            logger.warning(
                "No output adapters delivered integration.output (adapter=%s channel_id=%s)",
                adapter_name,
                channel_id,
            )

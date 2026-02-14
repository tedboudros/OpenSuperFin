"""Task handler that invokes the central AI interface from scheduler triggers."""

from __future__ import annotations

import logging
from typing import Any

from core.bus import AsyncIOBus
from core.models.events import Event, EventTypes
from core.models.tasks import TaskResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "ai_runner",
    "display_name": "AI Runner",
    "description": "Run the central AI interface from cron tasks using a single prompt",
    "category": "task_handler",
    "protocols": ["task_handler"],
    "class_name": "AIRunnerHandler",
    "pip_dependencies": [],
    "setup_instructions": "Use handler 'ai.run_prompt' with params.prompt and optional params.channel_id.",
    "config_fields": [],
}


class AIRunnerHandler:
    """Execute scheduled prompts using the same AI interface as Telegram chat."""

    def __init__(self, ai_interface: Any, bus: AsyncIOBus) -> None:
        self._ai = ai_interface
        self._bus = bus

    @property
    def name(self) -> str:
        return "ai.run_prompt"

    async def run(self, params: dict) -> TaskResult:
        prompt = str(params.get("prompt", "")).strip()
        if not prompt:
            return TaskResult(
                status="error",
                message="Missing required param: prompt",
            )

        channel_id = str(params.get("channel_id", "default"))
        source = str(params.get("source", "scheduler"))
        adapter = params.get("adapter")

        response = await self._ai.handle_scheduled_prompt(
            prompt=prompt,
            channel_id=channel_id,
            source=source,
            persist_output=True,
        )

        await self._bus.publish(Event(
            type=EventTypes.INTEGRATION_OUTPUT,
            source=self.name,
            payload={
                "text": response,
                "channel_id": channel_id,
                "adapter": adapter,
            },
        ))

        return TaskResult(
            status="success",
            message="AI ran and queued response for delivery via integration.output",
        )

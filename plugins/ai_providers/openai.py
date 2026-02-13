"""OpenAI LLM provider -- calls the OpenAI-compatible chat API via httpx.

No SDK dependency. Works with any OpenAI-compatible API (OpenAI, Azure, local).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.protocols import ToolCallResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "openai",
    "display_name": "OpenAI (GPT)",
    "description": "GPT models via the OpenAI API (also works with compatible APIs)",
    "category": "ai_provider",
    "protocols": ["llm"],
    "class_name": "OpenAIProvider",
    "pip_dependencies": [],
    "setup_instructions": """
1. Go to platform.openai.com
2. Navigate to API Keys
3. Create a new secret key
4. Paste it below
""",
    "config_fields": [
        {
            "key": "api_key",
            "label": "API Key",
            "type": "secret",
            "required": True,
            "env_var": "OPENAI_API_KEY",
            "description": "Your OpenAI API key",
            "placeholder": "sk-...",
        },
        {
            "key": "model",
            "label": "Model",
            "type": "string",
            "required": False,
            "default": "gpt-4o",
            "description": "Model name to use",
            "placeholder": "gpt-4o",
        },
        {
            "key": "temperature",
            "label": "Temperature",
            "type": "number",
            "required": False,
            "default": 0.3,
            "description": "Sampling temperature (0.0 = deterministic, 1.0 = creative)",
            "placeholder": "0.3",
        },
    ],
}

_DEFAULT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    """LLM provider for OpenAI-compatible APIs.

    Implements the LLMProvider protocol.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = _DEFAULT_URL,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._url = base_url
        self._client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def name(self) -> str:
        return "openai"

    async def complete(self, messages: list[dict], **kwargs: Any) -> str:
        """Send messages and return the text response."""
        body = {
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
        }

        response = await self._client.post(self._url, json=body)
        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"] or ""

    async def tool_call(
        self,
        messages: list[dict],
        tools: list[dict],
        **kwargs: Any,
    ) -> ToolCallResult:
        """Send messages with tool definitions and return results."""
        body = {
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "tools": tools,
        }

        response = await self._client.post(self._url, json=body)
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})

        return ToolCallResult(
            text=choice.get("content", "") or "",
            tool_calls=choice.get("tool_calls", []),
            usage=usage,
        )

    async def close(self) -> None:
        await self._client.aclose()

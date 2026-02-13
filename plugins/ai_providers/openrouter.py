"""OpenRouter LLM provider -- unified API for many models via a single endpoint.

OpenRouter provides access to OpenAI, Anthropic, Google, Meta, and many other
models through one API. Uses OpenAI-compatible chat completions format.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.protocols import ToolCallResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "openrouter",
    "display_name": "OpenRouter",
    "description": "Unified API for multiple LLM providers (OpenAI, Anthropic, Google, Meta, etc.)",
    "category": "ai_provider",
    "protocols": ["llm"],
    "class_name": "OpenRouterProvider",
    "pip_dependencies": [],
    "setup_instructions": """
1. Go to openrouter.ai
2. Sign up or log in
3. Navigate to Keys section
4. Create a new API key
5. Paste it below

OpenRouter provides access to many models through one API (e.g., openai/gpt-4o).
""",
    "config_fields": [
        {
            "key": "api_key",
            "label": "API Key",
            "type": "secret",
            "required": True,
            "env_var": "OPENROUTER_API_KEY",
            "description": "Your OpenRouter API key",
            "placeholder": "sk-or-v1-...",
        },
        {
            "key": "model",
            "label": "Model",
            "type": "string",
            "required": False,
            "default": "openai/gpt-4o",
            "description": "Model identifier (provider/model format)",
            "placeholder": "openai/gpt-4o",
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

_DEFAULT_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider:
    """LLM provider for OpenRouter's unified API.

    Implements the LLMProvider protocol. Uses OpenAI-compatible format.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-4o",
        base_url: str = _DEFAULT_URL,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        site_name: str = "OpenSuperFin",
        site_url: str = "https://github.com/tedboudros/OpenSuperFin",
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
                "HTTP-Referer": site_url,
                "X-Title": site_name,
            },
        )

    @property
    def name(self) -> str:
        return "openrouter"

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

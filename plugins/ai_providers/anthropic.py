"""Anthropic LLM provider -- calls the Claude API via httpx.

No SDK dependency. Direct HTTP calls to the Anthropic messages API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.protocols import ToolCallResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "anthropic",
    "display_name": "Anthropic (Claude)",
    "description": "Claude models via the Anthropic API",
    "category": "ai_provider",
    "protocols": ["llm"],
    "class_name": "AnthropicProvider",
    "pip_dependencies": [],
    "setup_instructions": """
1. Go to console.anthropic.com
2. Navigate to API Keys
3. Create a new key
4. Paste it below
""",
    "config_fields": [
        {
            "key": "api_key",
            "label": "API Key",
            "type": "secret",
            "required": True,
            "env_var": "ANTHROPIC_API_KEY",
            "description": "Your Anthropic API key",
            "placeholder": "sk-ant-...",
        },
        {
            "key": "model",
            "label": "Model",
            "type": "string",
            "required": False,
            "default": "claude-sonnet-4-20250514",
            "description": "Model name to use",
            "placeholder": "claude-sonnet-4-20250514",
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

_DEFAULT_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    """LLM provider for the Anthropic Claude API.

    Implements the LLMProvider protocol.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._url = _DEFAULT_URL
        self._client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, messages: list[dict], **kwargs: Any) -> str:
        """Send messages and return the text response."""
        # Convert from OpenAI-style messages to Anthropic format
        system_msg = ""
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                user_messages.append(msg)

        body: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "messages": user_messages,
        }
        if system_msg:
            body["system"] = system_msg

        response = await self._client.post(self._url, json=body)
        response.raise_for_status()
        data = response.json()

        # Extract text from content blocks
        content = data.get("content", [])
        text_parts = [block["text"] for block in content if block.get("type") == "text"]
        return "\n".join(text_parts)

    async def tool_call(
        self,
        messages: list[dict],
        tools: list[dict],
        **kwargs: Any,
    ) -> ToolCallResult:
        """Send messages with tool definitions and return results."""
        # Convert tools from OpenAI format to Anthropic format
        anthropic_tools = []
        for tool in tools:
            func = tool.get("function", tool)
            anthropic_tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            })

        system_msg = ""
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                user_messages.append(msg)

        body: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "messages": user_messages,
            "tools": anthropic_tools,
        }
        if system_msg:
            body["system"] = system_msg

        response = await self._client.post(self._url, json=body)
        response.raise_for_status()
        data = response.json()

        content = data.get("content", [])
        usage = data.get("usage", {})

        # Extract text and tool calls
        text_parts = []
        tool_calls = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                # Normalize to OpenAI-style tool_call format for consistency
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": block["input"],
                    },
                })

        return ToolCallResult(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    async def close(self) -> None:
        await self._client.aclose()

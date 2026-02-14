"""Anthropic LLM provider -- calls the Claude API via httpx.

No SDK dependency. Direct HTTP calls to the Anthropic messages API.
"""

from __future__ import annotations

import json
import logging
import re
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

    @staticmethod
    def _data_url_to_anthropic_image(data_url: str) -> dict[str, Any] | None:
        """Convert data:image/...;base64,... URL to Anthropic image block."""
        value = str(data_url or "").strip()
        if not value.startswith("data:image/"):
            return None
        match = re.match(
            r"^data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=]+)$",
            value,
        )
        if not match:
            return None
        media_type, data = match.group(1), match.group(2)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        }

    def _normalize_content_for_anthropic(self, content: Any) -> Any:
        """Convert OpenAI-style multimodal parts into Anthropic content blocks."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type", "")).strip().lower()
                if ptype in {"text", "input_text"}:
                    text = str(part.get("text", "")).strip()
                    if text:
                        blocks.append({"type": "text", "text": text})
                    continue
                if ptype in {"image_url", "input_image"}:
                    image_url = part.get("image_url")
                    if isinstance(image_url, dict):
                        url = image_url.get("url")
                    else:
                        url = image_url
                    block = self._data_url_to_anthropic_image(str(url or ""))
                    if block is not None:
                        blocks.append(block)
                    continue
            if blocks:
                return blocks

        return str(content)

    def _split_messages(
        self,
        messages: list[dict],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Split system message and normalize user/assistant message contents."""
        system_msg = ""
        user_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role", "user"))
            content = msg.get("content", "")
            if role == "system":
                if isinstance(content, str):
                    system_msg = content
                else:
                    system_msg = str(content)
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    blocks: list[dict[str, Any]] = []
                    normalized = self._normalize_content_for_anthropic(content)
                    if isinstance(normalized, str):
                        text = normalized.strip()
                        if text:
                            blocks.append({"type": "text", "text": text})
                    elif isinstance(normalized, list):
                        for block in normalized:
                            if isinstance(block, dict) and block.get("type") == "text":
                                blocks.append(block)

                    for idx, tc in enumerate(tool_calls):
                        if not isinstance(tc, dict):
                            continue
                        func = tc.get("function", tc)
                        if not isinstance(func, dict):
                            continue
                        name = str(func.get("name", "")).strip()
                        if not name:
                            continue
                        tool_id = str(tc.get("id") or f"{name}_{idx + 1}")
                        raw_args = func.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                parsed_args = json.loads(raw_args)
                            except Exception:
                                parsed_args = {}
                        elif isinstance(raw_args, dict):
                            parsed_args = raw_args
                        else:
                            parsed_args = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": parsed_args if isinstance(parsed_args, dict) else {},
                        })
                    user_messages.append({
                        "role": "assistant",
                        "content": blocks if blocks else "",
                    })
                    continue

            if role == "tool":
                tool_use_id = str(msg.get("tool_call_id", "")).strip()
                if not tool_use_id:
                    tool_use_id = str(msg.get("name", "tool")).strip() or "tool"
                normalized = self._normalize_content_for_anthropic(content)
                if isinstance(normalized, list):
                    text_parts = []
                    for block in normalized:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = str(block.get("text", "")).strip()
                            if text:
                                text_parts.append(text)
                    result_text = "\n".join(text_parts).strip() or str(content)
                else:
                    result_text = str(normalized)
                user_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_text,
                    }],
                })
                continue

            user_messages.append({
                "role": role,
                "content": self._normalize_content_for_anthropic(content),
            })
        return system_msg, user_messages

    async def complete(self, messages: list[dict], **kwargs: Any) -> str:
        """Send messages and return the text response."""
        system_msg, user_messages = self._split_messages(messages)

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

        system_msg, user_messages = self._split_messages(messages)

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

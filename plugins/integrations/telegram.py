"""Telegram integration -- bidirectional transport layer.

Input: Receives user messages and forwards them to the AI interface.
Output: Sends signals, memos, alerts, and AI responses.

This plugin is a DUMB PIPE. It does NOT classify messages, parse trades,
or understand intent. All intelligence lives in the AI interface which
uses tool-calling to understand what the user wants in any language.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import httpx

from core.models.memos import InvestmentMemo
from core.models.signals import Signal
from core.protocols import DeliveryResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "telegram",
    "display_name": "Telegram",
    "description": "Send and receive messages via Telegram bot",
    "category": "integration",
    "protocols": ["input", "output"],
    "class_name": "TelegramIntegration",
    "pip_dependencies": [],
    "setup_instructions": """
1. Open Telegram and message @BotFather
2. Send /newbot and follow the prompts to create a bot
3. Copy the bot token (looks like 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11)
4. Start a chat with your bot, or add it to a group
5. To find your chat_id, send a message to the bot then visit:
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   Look for "chat":{"id": YOUR_CHAT_ID} in the response
""",
    "config_fields": [
        {
            "key": "bot_token",
            "label": "Bot Token",
            "type": "secret",
            "required": True,
            "env_var": "TELEGRAM_BOT_TOKEN",
            "description": "Telegram bot token from @BotFather",
            "placeholder": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        },
        {
            "key": "chat_id",
            "label": "Chat ID",
            "type": "string",
            "required": True,
            "description": "Chat or group ID to send/receive messages",
            "placeholder": "123456789",
        },
        {
            "key": "direction",
            "label": "Direction",
            "type": "choice",
            "choices": ["both", "input", "output"],
            "default": "both",
            "description": "Use for input only, output only, or both",
        },
    ],
}

_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramIntegration:
    """Bidirectional Telegram transport.

    Implements both InputAdapter and OutputAdapter protocols.

    Input side: polls for messages, forwards raw text to registered callback.
    Output side: sends formatted messages to configured channels.
    """

    def __init__(
        self,
        bot_token: str,
        channels: list[dict] | None = None,
    ) -> None:
        self._token = bot_token
        self._base_url = _API_BASE.format(token=bot_token)
        self._client = httpx.AsyncClient(timeout=30.0)
        self._channels = channels or []
        self._callbacks: list[Callable[[dict], Coroutine[Any, Any, None]]] = []
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._last_update_id = 0

        # Build lookup maps
        self._input_channels = {
            ch["chat_id"]: ch for ch in self._channels
            if ch.get("direction") in ("both", "input")
        }
        self._output_channels = [
            ch for ch in self._channels
            if ch.get("direction") in ("both", "output")
        ]

    @property
    def name(self) -> str:
        return "telegram"

    # ------------------------------------------------------------------
    # InputAdapter protocol
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start polling for Telegram updates."""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram input started (polling %d input channel(s))", len(self._input_channels))

    async def stop(self) -> None:
        """Stop polling."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
        logger.info("Telegram integration stopped")

    def on_message(self, callback: Callable[[dict], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for incoming messages."""
        self._callbacks.append(callback)

    async def _poll_loop(self) -> None:
        """Long-poll the Telegram getUpdates API."""
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._process_update(update)
            except httpx.HTTPError:
                logger.exception("Telegram polling error")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in Telegram poll loop")
                await asyncio.sleep(5)

    async def _get_updates(self) -> list[dict]:
        """Fetch new updates from Telegram."""
        response = await self._client.get(
            f"{self._base_url}/getUpdates",
            params={
                "offset": self._last_update_id + 1,
                "timeout": 30,
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=35.0,
        )
        data = response.json()

        if not data.get("ok"):
            logger.warning("Telegram API error: %s", data)
            return []

        updates = data.get("result", [])
        if updates:
            self._last_update_id = updates[-1]["update_id"]

        return updates

    async def _process_update(self, update: dict) -> None:
        """Forward a Telegram message to the registered callbacks.

        No classification, no parsing, no NLP. Just package the raw
        message and hand it off. The AI interface handles understanding.
        """
        message = update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = message.get("text", "")
        from_user = message.get("from", {})

        # Only process messages from configured input channels
        if chat_id not in self._input_channels:
            return

        channel = self._input_channels[chat_id]

        # Package the raw message -- no interpretation
        payload = {
            "source": "telegram",
            "channel_id": channel.get("id", chat_id),
            "chat_id": chat_id,
            "text": text,
            "from_user": from_user.get("username", from_user.get("first_name", "unknown")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.debug("Telegram message from %s: %s", payload["from_user"], text[:80])

        # Forward to all callbacks (the AI interface handles understanding)
        for callback in self._callbacks:
            try:
                await callback(payload)
            except Exception:
                logger.exception("Error in Telegram message callback")

    # ------------------------------------------------------------------
    # OutputAdapter protocol
    # ------------------------------------------------------------------

    async def send(self, signal: Signal, memo: InvestmentMemo | None = None) -> DeliveryResult:
        """Send a signal notification to all output channels."""
        if not self._output_channels:
            return DeliveryResult(success=False, adapter=self.name, message="No output channels configured")

        message = self._format_signal_message(signal, memo)
        success = True
        errors = []

        for channel in self._output_channels:
            chat_id = channel["chat_id"]
            try:
                await self._send_message(chat_id, message)
                logger.info("Sent signal to Telegram channel %s", channel.get("id", chat_id))
            except Exception as e:
                logger.exception("Failed to send to Telegram channel %s", chat_id)
                errors.append(str(e))
                success = False

        return DeliveryResult(
            success=success,
            adapter=self.name,
            message="; ".join(errors) if errors else "Delivered",
        )

    async def send_text(self, text: str, channel_id: str | None = None) -> None:
        """Send a raw text message to a specific channel or all output channels."""
        targets = self._output_channels
        if channel_id:
            targets = [ch for ch in self._channels if ch.get("id") == channel_id or ch.get("chat_id") == channel_id]

        for channel in targets:
            try:
                await self._send_message(channel["chat_id"], text)
            except Exception:
                logger.exception("Failed to send text to %s", channel.get("chat_id"))

    async def _send_message(self, chat_id: str, text: str) -> dict:
        """Send a message via the Telegram Bot API."""
        # Telegram has a 4096 char limit -- split if needed
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]

        result = {}
        for chunk in chunks:
            response = await self._client.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            result = response.json()
            if not result.get("ok"):
                # Retry without markdown if parsing fails
                response = await self._client.post(
                    f"{self._base_url}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                )
                result = response.json()
                if not result.get("ok"):
                    raise RuntimeError(f"Telegram API error: {result.get('description', 'unknown')}")

        return result

    def _format_signal_message(self, signal: Signal, memo: InvestmentMemo | None = None) -> str:
        """Format a signal into a Telegram message."""
        icon = {"buy": "🟢", "sell": "🔴", "hold": "🟡"}.get(signal.direction, "⚪")

        lines = [
            f"{icon} *{signal.direction.upper()} {signal.ticker}*",
            f"Confidence: {signal.confidence:.0%}",
        ]

        if signal.entry_target:
            lines.append(f"Entry: ${signal.entry_target:,.2f}")
        if signal.stop_loss:
            lines.append(f"Stop Loss: ${signal.stop_loss:,.2f}")
        if signal.take_profit:
            lines.append(f"Take Profit: ${signal.take_profit:,.2f}")
        if signal.horizon:
            lines.append(f"Horizon: {signal.horizon}")

        if signal.catalyst:
            lines.append(f"\n_{signal.catalyst}_")

        if memo and memo.executive_summary:
            summary = memo.executive_summary[:500]
            lines.append(f"\n{summary}")

        return "\n".join(lines)

"""Discord integration -- bidirectional transport layer.

Input: Polls configured Discord channels for new messages.
Output: Sends AI responses and notifications to configured channels.

This plugin is a DUMB PIPE. It forwards raw user text to the AI interface.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import httpx

from core.models.memos import InvestmentMemo
from core.models.signals import Signal
from core.protocols import DeliveryResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "discord",
    "display_name": "Discord",
    "description": "Send and receive messages via Discord bot",
    "category": "integration",
    "protocols": ["input", "output"],
    "class_name": "DiscordIntegration",
    "pip_dependencies": [],
    "setup_instructions": """
1. Create a bot in Discord Developer Portal
2. Copy the bot token
3. Invite bot to your server/channel with Send Messages + Read Message History permissions
4. Enable Message Content Intent in bot settings
5. Copy your Discord channel ID (Developer Mode -> right-click channel -> Copy ID)
""",
    "config_fields": [
        {
            "key": "bot_token",
            "label": "Bot Token",
            "type": "secret",
            "required": True,
            "env_var": "DISCORD_BOT_TOKEN",
            "description": "Discord bot token",
            "placeholder": "your-discord-bot-token",
        },
        {
            "key": "chat_id",
            "label": "Channel ID",
            "type": "string",
            "required": True,
            "description": "Discord channel ID to send/receive messages",
            "placeholder": "123456789012345678",
        },
        {
            "key": "direction",
            "label": "Direction",
            "type": "choice",
            "choices": ["both", "input", "output"],
            "default": "both",
            "description": "Use for input only, output only, or both",
        },
        {
            "key": "poll_interval_seconds",
            "label": "Poll Interval Seconds",
            "type": "number",
            "required": False,
            "default": 3,
            "description": "How often to poll channel messages",
            "placeholder": "3",
        },
    ],
}

_API_BASE = "https://discord.com/api/v10"


class DiscordIntegration:
    """Bidirectional Discord transport using REST polling."""

    def __init__(
        self,
        bot_token: str,
        channels: list[dict] | None = None,
        poll_interval_seconds: int = 3,
    ) -> None:
        self._token = bot_token
        self._channels = channels or []
        self._poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "ClawQuant/0.1",
            },
        )
        self._callbacks: list[Callable[[dict], Coroutine[Any, Any, None]]] = []
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._last_message_id: dict[str, str] = {}
        self._bot_user_id: str | None = None

        self._input_channels = {
            str(ch["chat_id"]): ch for ch in self._channels
            if ch.get("direction") in ("both", "input")
        }
        self._output_channels = [
            ch for ch in self._channels
            if ch.get("direction") in ("both", "output")
        ]

    @property
    def name(self) -> str:
        return "discord"

    # ------------------------------------------------------------------
    # InputAdapter protocol
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._bot_user_id = await self._fetch_bot_user_id()
        await self._bootstrap_offsets()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Discord input started (polling %d input channel(s))", len(self._input_channels))

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
        logger.info("Discord integration stopped")

    def on_message(self, callback: Callable[[dict], Coroutine[Any, Any, None]]) -> None:
        self._callbacks.append(callback)

    async def _fetch_bot_user_id(self) -> str | None:
        try:
            response = await self._client.get(f"{_API_BASE}/users/@me")
            response.raise_for_status()
            return str(response.json().get("id", ""))
        except Exception:
            logger.exception("Failed to fetch Discord bot identity")
            return None

    async def _bootstrap_offsets(self) -> None:
        for channel_id in self._input_channels:
            latest_id = await self._get_latest_message_id(channel_id)
            if latest_id:
                self._last_message_id[channel_id] = latest_id

    async def _get_latest_message_id(self, channel_id: str) -> str | None:
        response = await self._client.get(
            f"{_API_BASE}/channels/{channel_id}/messages",
            params={"limit": 1},
        )
        if response.status_code != 200:
            logger.warning("Discord bootstrap read failed for channel %s: %s", channel_id, response.status_code)
            return None
        rows = response.json()
        if not rows:
            return None
        return str(rows[0].get("id", ""))

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                for channel_id in self._input_channels:
                    await self._poll_channel(channel_id)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in Discord poll loop")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _poll_channel(self, channel_id: str) -> None:
        params: dict[str, Any] = {"limit": 50}
        last_id = self._last_message_id.get(channel_id)
        if last_id:
            params["after"] = last_id

        response = await self._client.get(
            f"{_API_BASE}/channels/{channel_id}/messages",
            params=params,
        )
        if response.status_code == 429:
            retry_after = float(response.json().get("retry_after", 1.0))
            await asyncio.sleep(retry_after)
            return
        if response.status_code != 200:
            logger.warning("Discord poll failed for channel %s: %s", channel_id, response.status_code)
            return

        rows = response.json()
        if not rows:
            return

        # Discord returns newest first; process oldest first.
        rows = sorted(rows, key=lambda msg: int(msg.get("id", "0")))

        for msg in rows:
            msg_id = str(msg.get("id", ""))
            if msg_id:
                self._last_message_id[channel_id] = msg_id

            author = msg.get("author", {})
            author_id = str(author.get("id", ""))
            if self._bot_user_id and author_id == self._bot_user_id:
                continue
            if author.get("bot"):
                continue

            text = (msg.get("content") or "").strip()
            if not text:
                continue

            channel = self._input_channels[channel_id]
            payload = {
                "source": "discord",
                "channel_id": channel.get("id", channel_id),
                "chat_id": channel_id,
                "text": text,
                "from_user": author.get("username", "unknown"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            for callback in self._callbacks:
                try:
                    await callback(payload)
                except Exception:
                    logger.exception("Error in Discord message callback")

    # ------------------------------------------------------------------
    # OutputAdapter protocol
    # ------------------------------------------------------------------

    async def send(self, signal: Signal, memo: InvestmentMemo | None = None) -> DeliveryResult:
        if not self._output_channels:
            return DeliveryResult(success=False, adapter=self.name, message="No output channels configured")

        message = self._format_signal_message(signal, memo)
        success = True
        errors = []

        for channel in self._output_channels:
            channel_id = str(channel["chat_id"])
            try:
                await self._send_message(channel_id, message)
                logger.info("Sent signal to Discord channel %s", channel.get("id", channel_id))
            except Exception as exc:
                logger.exception("Failed to send to Discord channel %s", channel_id)
                errors.append(str(exc))
                success = False

        return DeliveryResult(
            success=success,
            adapter=self.name,
            message="; ".join(errors) if errors else "Delivered",
        )

    async def send_text(self, text: str, channel_id: str | None = None) -> None:
        targets = self._output_channels
        if channel_id:
            targets = [ch for ch in self._channels if ch.get("id") == channel_id or str(ch.get("chat_id")) == channel_id]

        for channel in targets:
            try:
                await self._send_message(str(channel["chat_id"]), text)
            except Exception:
                logger.exception("Failed to send text to Discord channel %s", channel.get("chat_id"))

    async def _send_message(self, channel_id: str, text: str) -> dict:
        # Discord content limit is 2000 chars.
        chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or [""]

        result = {}
        for chunk in chunks:
            response = await self._client.post(
                f"{_API_BASE}/channels/{channel_id}/messages",
                json={"content": chunk},
            )
            if response.status_code == 429:
                retry_after = float(response.json().get("retry_after", 1.0))
                await asyncio.sleep(retry_after)
                response = await self._client.post(
                    f"{_API_BASE}/channels/{channel_id}/messages",
                    json={"content": chunk},
                )

            result = response.json()
            if response.status_code >= 400:
                raise RuntimeError(f"Discord API error {response.status_code}: {result}")

        return result

    def _format_signal_message(self, signal: Signal, memo: InvestmentMemo | None = None) -> str:
        icon = {"buy": "🟢", "sell": "🔴", "hold": "🟡"}.get(signal.direction, "⚪")

        lines = [
            f"{icon} **{signal.direction.upper()} {signal.ticker}**",
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
            lines.append(f"\n{signal.catalyst}")

        if memo and memo.executive_summary:
            summary = memo.executive_summary[:500]
            lines.append(f"\n{summary}")

        return "\n".join(lines)

"""Market news task handler and shared news fetch utilities."""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from core.bus import AsyncIOBus
from core.models.events import Event, EventTypes
from core.models.tasks import TaskResult
from core.registry import PluginRegistry

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "news",
    "display_name": "Market News Briefing",
    "description": "Fetch market headlines and send a scheduled news briefing",
    "category": "task_handler",
    "protocols": ["task_handler"],
    "class_name": "NewsBriefHandler",
    "pip_dependencies": [],
    "setup_instructions": "Use handler name 'news.briefing' with any schedule you want.",
    "config_fields": [
        {
            "key": "default_limit",
            "label": "Default headlines",
            "type": "number",
            "required": False,
            "default": 8,
            "description": "Default number of headlines in each briefing",
            "placeholder": "8",
        },
    ],
}

_GENERAL_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
]


def _parse_as_of(as_of: str | None) -> datetime | None:
    if not as_of:
        return None
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


async def fetch_market_news(topic: str | None = None, limit: int = 8, as_of: str | None = None) -> list[dict]:
    """Fetch market headlines from RSS feeds."""
    cutoff = _parse_as_of(as_of)
    urls = list(_GENERAL_FEEDS)
    if topic:
        query = quote(topic)
        ticker = quote(topic.upper())
        urls.append(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US")
        urls.append(f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en")
        urls.append(f"https://news.google.com/rss/search?q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en")

    items: list[dict] = []
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "ClawQuant/0.1"}) as client:
        for url in urls:
            try:
                response = await client.get(url)
                if response.status_code != 200:
                    continue
                items.extend(_parse_rss_items(response.text))
            except Exception:
                logger.exception("Failed to fetch RSS feed: %s", url)

    # Deduplicate by title while keeping arrival order.
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        key = item["title"].strip().lower()
        if not key or key in seen:
            continue
        pub_dt = _to_datetime(item.get("published"))
        if cutoff and pub_dt and pub_dt > cutoff:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _parse_rss_items(xml_text: str) -> list[dict]:
    """Parse RSS XML into a normalized list of headline dicts."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    out: list[dict] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "link": link,
            "published": published,
        })
    return out


class NewsBriefHandler:
    """Scheduled handler that sends a market news briefing."""

    def __init__(self, registry: PluginRegistry, bus: AsyncIOBus, default_limit: int = 8) -> None:
        self._registry = registry
        self._bus = bus
        self._default_limit = default_limit

    @property
    def name(self) -> str:
        return "news.briefing"

    def get_tools(self) -> list[dict]:
        """Expose plugin-specific tools for the AI interface."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_news",
                    "description": "Browse latest market news headlines. Optionally focus on a ticker/topic.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string",
                                "description": "Optional ticker or topic filter (e.g., NVDA, rates, oil)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of headlines (default: 8)",
                            },
                            "as_of": {
                                "type": "string",
                                "description": "Optional ISO datetime cutoff for sandbox mode. Only return headlines published at or before this time.",
                            },
                        },
                    },
                },
            }
        ]

    async def call_tool(
        self,
        name: str,
        args: dict,
        source: str | None = None,
        interface: object | None = None,
    ) -> str | None:
        """Execute plugin-provided tools."""
        if name != "get_news":
            return None

        topic = args.get("topic")
        limit = int(args.get("limit", self._default_limit))
        as_of = args.get("as_of")
        headlines = await fetch_market_news(topic=topic, limit=limit, as_of=as_of)
        if not headlines:
            if as_of:
                return "No market headlines found for that query before the requested as_of time."
            return "No market headlines available right now."

        lines = []
        for idx, item in enumerate(headlines, start=1):
            title = item["title"]
            link = item.get("link", "")
            lines.append(f"{idx}. {title}" + (f"\n   {link}" if link else ""))
        topic_text = f" for {topic}" if topic else ""
        return f"Latest headlines{topic_text}:\n" + "\n".join(lines)

    async def run(self, params: dict) -> TaskResult:
        topic = params.get("topic")
        limit = int(params.get("limit", self._default_limit))
        as_of = params.get("as_of")
        channel_id = params.get("channel_id")
        adapter = params.get("adapter")
        summarize = bool(params.get("summarize", True))

        headlines = await fetch_market_news(topic=topic, limit=limit, as_of=as_of)
        if not headlines:
            return TaskResult(status="no_action", message="No news headlines available")

        message = self._format_message(headlines, topic=topic)
        if summarize:
            summary = await self._summarize(headlines, topic=topic)
            if summary:
                message = f"{message}\n\n*Quick Read*\n{summary}"

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
            message="Queued news briefing for delivery via integration.output",
        )

    def _format_message(self, headlines: list[dict], topic: str | None = None) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header_topic = f" ({topic.upper()})" if topic else ""
        lines = [f"*Market News Brief{header_topic}*", f"_{now}_", ""]
        for idx, item in enumerate(headlines, start=1):
            title = item["title"]
            link = item.get("link", "")
            if link:
                lines.append(f"{idx}. {title}\n{link}")
            else:
                lines.append(f"{idx}. {title}")
        return "\n".join(lines)

    async def _summarize(self, headlines: list[dict], topic: str | None = None) -> str:
        providers = self._registry.get_all("llm")
        if not providers:
            return ""
        llm = providers[0]

        headline_blob = "\n".join(f"- {item['title']}" for item in headlines)
        prompt = (
            "Summarize these market headlines in 4 concise bullets with trade-relevant framing. "
            "Mention potential impact on risk sentiment, rates, mega-cap tech, and commodities when relevant.\n\n"
            f"Topic: {topic or 'broad market'}\n"
            f"Headlines:\n{headline_blob}"
        )
        try:
            return (await llm.complete([{"role": "user", "content": prompt}])).strip()
        except Exception:
            logger.exception("Failed to summarize headlines")
            return ""

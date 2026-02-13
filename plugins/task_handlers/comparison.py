"""Portfolio comparison task handler -- the learning loop.

Runs on a schedule (default: weekly). Finds divergences between the AI and
human portfolios, evaluates outcomes, and generates Memory entries via LLM.

This is where the AI and human teach each other.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.events import Event, EventTypes
from core.models.memories import Memory
from core.models.signals import Position
from core.models.tasks import TaskResult
from core.protocols import LLMProvider
from core.registry import PluginRegistry

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "comparison",
    "display_name": "Portfolio Comparison",
    "description": "Weekly AI-vs-human portfolio comparison that generates learning memories",
    "category": "task_handler",
    "protocols": ["task_handler"],
    "class_name": "ComparisonHandler",
    "pip_dependencies": [],
    "setup_instructions": "Runs automatically on schedule. Compares AI and human portfolios, generates memories from divergences.",
    "config_fields": [
        {
            "key": "min_outcome_days",
            "label": "Min days before evaluating",
            "type": "number",
            "required": False,
            "default": 7,
            "description": "Wait at least this many days before judging a divergence",
            "placeholder": "7",
        },
        {
            "key": "comparison_schedule",
            "label": "Schedule (cron)",
            "type": "string",
            "required": False,
            "default": "0 9 * * 0",
            "description": "Cron expression for when to run (default: Sunday 9am)",
            "placeholder": "0 9 * * 0",
        },
    ],
}

COMPARISON_PROMPT = """You are analyzing a divergence between an AI trading system and a human trader.

Divergence details:
- Signal: {signal_direction} {ticker}
- AI action: {ai_action}
- Human action: {human_action}
- Outcome: {outcome}
- AI P&L: {ai_pnl}
- Human P&L: {human_pnl}

Analyze this divergence. Respond in JSON:
{{
    "who_was_right": "ai" | "human" | "both" | "neither",
    "lesson": "A concise lesson learned (2-3 sentences). What should be done differently next time?",
    "tags": ["tag1", "tag2", "tag3"],
    "confidence_impact": -0.1 to 0.1
}}

Tags should include the ticker, sector, and any relevant themes (e.g., "earnings", "macro", "momentum").
confidence_impact: positive means the AI should be MORE confident in similar situations, negative means LESS."""


class ComparisonHandler:
    """Compares AI vs human portfolios and generates learning memories.

    Implements the TaskHandler protocol.
    """

    def __init__(
        self,
        store: Store,
        bus: AsyncIOBus,
        registry: PluginRegistry,
        min_outcome_days: int = 7,
    ) -> None:
        self._store = store
        self._bus = bus
        self._registry = registry
        self._min_outcome_days = min_outcome_days

    @property
    def name(self) -> str:
        return "comparison.weekly"

    async def run(self, params: dict) -> TaskResult:
        """Find divergences, evaluate outcomes, generate memories."""
        ai_positions = self._store.list_json("positions/ai", Position)
        human_positions = self._store.list_json("positions/human", Position)

        # Build lookup maps
        ai_map = {p.ticker: p for p in ai_positions}
        human_map = {p.ticker: p for p in human_positions}

        # Find all tickers across both portfolios
        all_tickers = set(ai_map.keys()) | set(human_map.keys())

        divergences = []
        for ticker in all_tickers:
            ai_pos = ai_map.get(ticker)
            human_pos = human_map.get(ticker)
            divergence = self._classify_divergence(ticker, ai_pos, human_pos)
            if divergence:
                divergences.append(divergence)

        if not divergences:
            return TaskResult(
                status="no_action",
                message="No divergences found between AI and human portfolios",
            )

        # Generate memories for divergences with enough outcome time
        memories_created = 0
        for div in divergences:
            # Check if we already have a memory for this divergence
            existing = self._store.search_memories(ticker=div["ticker"], limit=50)
            already_covered = any(
                self._is_same_divergence(mid, div) for mid in existing
            )
            if already_covered:
                continue

            # Check if enough time has passed
            if not self._has_enough_outcome_time(div):
                continue

            memory = await self._generate_memory(div)
            if memory:
                # Save memory as JSON file
                self._store.write_json("memories", f"{memory.id}.json", memory)
                # Index in SQLite
                self._store.index_memory(memory)

                # Publish event
                event = Event(
                    type=EventTypes.MEMORY_CREATED,
                    source="comparison",
                    payload={"memory_id": memory.id, "ticker": div["ticker"]},
                )
                await self._bus.publish(event)

                memories_created += 1
                logger.info(
                    "Created memory: %s (%s was right about %s)",
                    memory.id, memory.who_was_right, div["ticker"],
                )

        return TaskResult(
            status="success",
            message=f"Found {len(divergences)} divergences, created {memories_created} memories",
        )

    def _classify_divergence(
        self,
        ticker: str,
        ai_pos: Position | None,
        human_pos: Position | None,
    ) -> dict | None:
        """Classify the type of divergence between AI and human positions."""
        # Both have the position and agree -- no divergence
        if ai_pos and human_pos:
            if ai_pos.status == human_pos.status:
                return None
            # Timing or price divergence
            if ai_pos.status in ("monitoring", "closed") and human_pos.status in ("monitoring", "closed"):
                if ai_pos.close_price and human_pos.close_price and ai_pos.close_price != human_pos.close_price:
                    return {
                        "ticker": ticker,
                        "type": "timing_divergence",
                        "ai_action": f"{ai_pos.direction} at {ai_pos.entry_price}",
                        "human_action": f"{human_pos.direction} at {human_pos.entry_price}",
                        "ai_pos": ai_pos,
                        "human_pos": human_pos,
                        "opened_at": ai_pos.opened_at,
                    }

        # AI has position, human skipped
        if ai_pos and human_pos and human_pos.status == "skipped":
            return {
                "ticker": ticker,
                "type": "human_skipped",
                "ai_action": f"{ai_pos.direction} at {ai_pos.entry_price}",
                "human_action": f"Skipped: {human_pos.user_notes or 'no reason given'}",
                "ai_pos": ai_pos,
                "human_pos": human_pos,
                "opened_at": ai_pos.opened_at,
            }

        # AI has position, human doesn't exist at all (assumed via timeout)
        if ai_pos and not human_pos:
            # This might be normal (assumed execution), skip
            return None

        # Human has position, AI doesn't (human-initiated trade)
        if human_pos and not ai_pos:
            if human_pos.signal_id is None:  # user-initiated
                return {
                    "ticker": ticker,
                    "type": "human_initiated",
                    "ai_action": "No signal",
                    "human_action": f"{human_pos.direction} at {human_pos.entry_price} ({human_pos.user_notes or 'no reason'})",
                    "ai_pos": None,
                    "human_pos": human_pos,
                    "opened_at": human_pos.opened_at,
                }

        return None

    def _has_enough_outcome_time(self, divergence: dict) -> bool:
        """Check if enough time has passed to evaluate the outcome."""
        opened_at = divergence.get("opened_at")
        if not opened_at:
            return False

        now = datetime.now(timezone.utc)
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at)

        days_elapsed = (now - opened_at).days
        return days_elapsed >= self._min_outcome_days

    def _is_same_divergence(self, memory_id: str, divergence: dict) -> bool:
        """Check if a memory already covers this divergence."""
        mem = self._store.read_json("memories", f"{memory_id}.json", Memory)
        if not mem:
            return False
        # Same ticker and same signal_id means already covered
        ai_pos = divergence.get("ai_pos")
        if ai_pos and mem.signal_id == ai_pos.signal_id:
            return True
        return False

    async def _generate_memory(self, divergence: dict) -> Memory | None:
        """Use the LLM to generate a structured memory from a divergence."""
        ai_pos: Position | None = divergence.get("ai_pos")
        human_pos: Position | None = divergence.get("human_pos")

        # Calculate P&L
        ai_pnl = "N/A"
        human_pnl = "N/A"
        outcome = "Outcome not yet determined"

        if ai_pos:
            if ai_pos.realized_pnl is not None:
                ai_pnl = f"${ai_pos.realized_pnl:,.2f} ({ai_pos.realized_pnl_percent or 0:.1f}%)"
            elif ai_pos.pnl is not None:
                ai_pnl = f"${ai_pos.pnl:,.2f} ({ai_pos.pnl_percent or 0:.1f}%) unrealized"

        if human_pos and human_pos.status != "skipped":
            if human_pos.realized_pnl is not None:
                human_pnl = f"${human_pos.realized_pnl:,.2f} ({human_pos.realized_pnl_percent or 0:.1f}%)"
            elif human_pos.pnl is not None:
                human_pnl = f"${human_pos.pnl:,.2f} ({human_pos.pnl_percent or 0:.1f}%) unrealized"
        elif human_pos and human_pos.status == "skipped":
            human_pnl = "$0 (skipped)"

        if ai_pos and ai_pos.current_price:
            change = ((ai_pos.current_price - ai_pos.entry_price) / ai_pos.entry_price) * 100
            outcome = f"{divergence['ticker']} moved from ${ai_pos.entry_price:,.2f} to ${ai_pos.current_price:,.2f} ({change:+.1f}%)"

        # Get LLM to generate the memory
        providers = self._registry.get_all("llm")
        if not providers:
            logger.warning("No LLM providers available for memory generation")
            return None

        llm: LLMProvider = providers[0]

        prompt = COMPARISON_PROMPT.format(
            signal_direction=ai_pos.direction if ai_pos else "none",
            ticker=divergence["ticker"],
            ai_action=divergence["ai_action"],
            human_action=divergence["human_action"],
            outcome=outcome,
            ai_pnl=ai_pnl,
            human_pnl=human_pnl,
        )

        try:
            response = await llm.complete([
                {"role": "system", "content": "You analyze trading divergences between an AI and a human."},
                {"role": "user", "content": prompt},
            ])

            return self._parse_memory_response(response, divergence, ai_pos)
        except Exception:
            logger.exception("Failed to generate memory for %s", divergence["ticker"])
            return None

    def _parse_memory_response(
        self,
        response: str,
        divergence: dict,
        ai_pos: Position | None,
    ) -> Memory | None:
        """Parse LLM response into a Memory object."""
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1])
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Could not parse memory response as JSON")
            return None

        return Memory(
            signal_id=ai_pos.signal_id if ai_pos else None,
            divergence_type=divergence["type"],
            ai_action=divergence["ai_action"],
            human_action=divergence["human_action"],
            who_was_right=data.get("who_was_right", "neither"),
            lesson=data.get("lesson", ""),
            tags=data.get("tags", [divergence["ticker"]]),
            confidence_impact=float(data.get("confidence_impact", 0)),
        )

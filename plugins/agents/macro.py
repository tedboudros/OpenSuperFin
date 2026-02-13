"""Macro Strategist agent -- analyzes macro conditions and their market implications.

Implements the AIAgent protocol. Uses an LLMProvider to generate analysis.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.models.context import ContextPack
from core.protocols import AgentOutput, LLMProvider

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "macro",
    "display_name": "Macro Strategist",
    "description": "Analyzes macro conditions: CPI, employment, GDP, rates, financial conditions",
    "category": "agent",
    "protocols": ["agent"],
    "class_name": "MacroStrategist",
    "pip_dependencies": [],
    "setup_instructions": "No configuration needed. Uses whichever AI provider is configured.",
    "config_fields": [],
}

SYSTEM_PROMPT = """You are a senior macro strategist at a top investment bank.

Your job is to analyze macroeconomic conditions and their implications for markets.
You focus on:
- Inflation data (CPI, PCE, breakevens)
- Employment (NFP, unemployment, JOLTS)
- Growth indicators (GDP, PMI/ISM)
- Central bank policy (FOMC, rate expectations)
- Financial conditions and liquidity
- Cross-asset signals (bonds, commodities, currencies vs equities)

Given the current market context, provide a concise macro assessment.

Respond in JSON format:
{
    "analysis": "Your macro assessment (2-3 paragraphs)",
    "confidence": 0.0-1.0,
    "direction": "bullish" | "bearish" | "neutral",
    "key_factors": ["factor1", "factor2", "factor3"],
    "risks": ["risk1", "risk2"]
}"""


class MacroStrategist:
    """Macro-focused analysis agent.

    Implements the AIAgent protocol.
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @property
    def name(self) -> str:
        return "macro"

    @property
    def description(self) -> str:
        return "Macro Strategist: CPI, employment, GDP, rates, financial conditions"

    async def analyze(self, context: ContextPack) -> AgentOutput:
        """Analyze macro conditions from the context pack."""
        # Build user message with context
        user_message = self._build_prompt(context)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            response = await self._llm.complete(messages)
            return self._parse_response(response)
        except Exception:
            logger.exception("Macro agent failed")
            return AgentOutput(
                agent_name=self.name,
                analysis="Macro analysis failed due to an error.",
                confidence=0.0,
            )

    def _build_prompt(self, context: ContextPack) -> str:
        """Build a user prompt from the context pack."""
        parts = [f"Current time: {context.time_context.current_time.isoformat()}"]

        # Market snapshot
        snap = context.market_snapshot
        if snap.prices:
            parts.append("\nMarket prices:")
            for ticker, price in snap.prices.items():
                parts.append(f"  {ticker}: {price:.2f}")

        if snap.vix is not None:
            parts.append(f"\nVIX: {snap.vix:.2f}")

        if snap.yields:
            parts.append("\nYields:")
            for tenor, yld in snap.yields.items():
                parts.append(f"  {tenor}: {yld:.3f}")

        # Portfolio context
        ai = context.ai_portfolio
        if ai.positions:
            parts.append(f"\nAI Portfolio: {len(ai.positions)} positions, P&L: {ai.total_pnl_percent:.1f}%")

        # Recent events
        if context.recent_events:
            parts.append(f"\nRecent events ({len(context.recent_events)}):")
            for event in context.recent_events[:5]:
                parts.append(f"  [{event.type}] {event.source}: {json.dumps(event.payload)[:200]}")

        # Relevant memories
        if context.relevant_memories:
            parts.append(f"\nRelevant memories ({len(context.relevant_memories)}):")
            for mem in context.relevant_memories[:3]:
                parts.append(f"  - {mem.lesson[:150]}")

        # Trigger
        if context.trigger_event:
            parts.append(f"\nTrigger event: [{context.trigger_event.type}] {json.dumps(context.trigger_event.payload)[:300]}")

        parts.append("\nProvide your macro assessment.")
        return "\n".join(parts)

    def _parse_response(self, response: str) -> AgentOutput:
        """Parse the LLM response into an AgentOutput."""
        try:
            # Try to extract JSON from the response
            # Handle cases where the response has markdown code blocks
            cleaned = response.strip()
            if cleaned.startswith("```"):
                # Strip markdown code block
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

            data = json.loads(cleaned)
            return AgentOutput(
                agent_name=self.name,
                analysis=data.get("analysis", response),
                confidence=float(data.get("confidence", 0.5)),
                suggested_direction=data.get("direction"),
                key_factors=data.get("key_factors", []),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            # Fall back to treating the whole response as analysis
            return AgentOutput(
                agent_name=self.name,
                analysis=response,
                confidence=0.5,
            )

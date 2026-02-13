"""Orchestrator -- multi-agent pipeline that produces investment memos and signals.

When triggered by events, the orchestrator:
1. Assembles a ContextPack (market data, portfolios, memories)
2. Runs the agent chain
3. Synthesizes results into an InvestmentMemo
4. Produces a Signal and publishes signal.proposed
5. Optionally creates follow-up tasks
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.context import ContextPack, PortfolioSummary
from core.models.events import Event, EventTypes
from core.models.market import MarketSnapshot
from core.models.memos import InvestmentMemo, Scenario
from core.models.signals import Signal
from core.protocols import AgentOutput, LLMProvider
from core.registry import PluginRegistry
from core.time_context import TimeContext
from engine.memory import MemoryRetriever
from risk.portfolio import PortfolioTracker

logger = logging.getLogger(__name__)

SYNTHESIS_PROMPT = """You are the Chief Investment Officer synthesizing analyses from your team.

Given the following agent analyses, produce a structured investment decision.

{agent_analyses}

Trigger event: {trigger}

Respond in JSON:
{{
    "executive_summary": "2-3 sentence thesis",
    "catalyst": "what happened and why it matters",
    "market_context": "current regime and conditions",
    "pricing_vs_view": "where the market is priced vs our view",
    "scenarios": [
        {{"name": "Bull", "probability": 0.0-1.0, "description": "...", "target_price": null}},
        {{"name": "Base", "probability": 0.0-1.0, "description": "...", "target_price": null}},
        {{"name": "Bear", "probability": 0.0-1.0, "description": "...", "target_price": null}}
    ],
    "trade_expression": "how to express the view",
    "entry_plan": "entry strategy",
    "risks": ["risk1", "risk2"],
    "monitoring_plan": "what to watch",
    "signal": {{
        "ticker": "TICKER",
        "direction": "buy" | "sell" | "hold",
        "confidence": 0.0-1.0,
        "entry_target": null,
        "stop_loss": null,
        "take_profit": null,
        "horizon": "1-3 months"
    }}
}}

If you don't have enough conviction for a trade, set direction to "hold" with an explanation."""


class Orchestrator:
    """Multi-agent pipeline coordinator.

    Assembles context, runs agents, synthesizes into memos and signals.
    """

    def __init__(
        self,
        bus: AsyncIOBus,
        store: Store,
        registry: PluginRegistry,
        portfolio: PortfolioTracker,
        memory_retriever: MemoryRetriever,
    ) -> None:
        self._bus = bus
        self._store = store
        self._registry = registry
        self._portfolio = portfolio
        self._memory = memory_retriever

    async def analyze(
        self,
        trigger_event: Event,
        time_context: TimeContext | None = None,
    ) -> tuple[InvestmentMemo, Signal | None]:
        """Run the full analysis pipeline.

        Returns the investment memo and optionally a signal (None if hold/no trade).
        """
        tc = time_context or TimeContext.now()

        # Step 1: Assemble context pack
        context = await self._assemble_context(trigger_event, tc)

        # Publish context.assembled
        assembled_event = trigger_event.derive(
            type=EventTypes.CONTEXT_ASSEMBLED,
            source="orchestrator",
        )
        await self._bus.publish(assembled_event)

        # Step 2: Run agent chain
        agent_outputs = await self._run_agents(context)

        # Step 3: Synthesize into memo + signal
        memo, signal = await self._synthesize(
            agent_outputs, trigger_event, context, tc
        )

        # Step 4: Persist memo as Markdown
        memo_filename = (
            f"{tc.current_time.strftime('%Y-%m-%d')}"
            f"_{signal.ticker if signal else 'analysis'}"
            f"_{signal.direction if signal else 'hold'}.md"
        )
        self._store.write_markdown("memos", memo_filename, memo.to_markdown())

        # Publish memo.created
        memo_event = trigger_event.derive(
            type=EventTypes.MEMO_CREATED,
            source="orchestrator",
            payload={"memo_id": memo.id, "filename": memo_filename},
        )
        await self._bus.publish(memo_event)

        # Step 5: If we have a signal (not hold), publish signal.proposed
        if signal and signal.direction != "hold":
            signal.memo_id = memo.id
            signal.correlation_id = trigger_event.correlation_id

            # Save signal as JSON
            self._store.write_json("signals", f"{signal.id}.json", signal)

            signal_event = trigger_event.derive(
                type=EventTypes.SIGNAL_PROPOSED,
                source="orchestrator",
                payload=signal.model_dump(mode="json"),
            )
            await self._bus.publish(signal_event)

        return memo, signal

    async def _assemble_context(
        self, trigger_event: Event, tc: TimeContext
    ) -> ContextPack:
        """Build the context pack for agent analysis."""
        # Get market snapshot
        snapshot = MarketSnapshot(timestamp=tc.current_time)

        # Get prices for watchlist tickers from market data providers
        providers = self._registry.get_all("market_data")
        for provider in providers:
            # Get latest prices from SQLite
            pass  # Will be populated when market data is synced

        # Get portfolio summaries
        ai_portfolio = self._portfolio.get_summary("ai")
        human_portfolio = self._portfolio.get_summary("human")

        # Get relevant memories
        # Try to extract a ticker from the trigger event
        ticker_hint = trigger_event.payload.get("ticker")
        tags_hint = trigger_event.payload.get("tags", [])
        memories = self._memory.retrieve(ticker=ticker_hint, tags=tags_hint)

        return ContextPack(
            time_context=tc,
            market_snapshot=snapshot,
            ai_portfolio=ai_portfolio,
            human_portfolio=human_portfolio,
            trigger_event=trigger_event,
            relevant_memories=memories,
        )

    async def _run_agents(self, context: ContextPack) -> list[AgentOutput]:
        """Run all registered agents against the context pack."""
        agents = self._registry.get_all("agent")
        outputs: list[AgentOutput] = []

        for agent in agents:
            try:
                logger.info("Running agent: %s", agent.name)
                output = await agent.analyze(context)
                outputs.append(output)
                logger.info(
                    "Agent %s: confidence=%.2f direction=%s",
                    agent.name,
                    output.confidence,
                    output.suggested_direction or "none",
                )
            except Exception:
                logger.exception("Agent %s failed", agent.name)

        return outputs

    async def _synthesize(
        self,
        agent_outputs: list[AgentOutput],
        trigger_event: Event,
        context: ContextPack,
        tc: TimeContext,
    ) -> tuple[InvestmentMemo, Signal | None]:
        """Synthesize agent outputs into an investment memo and signal."""
        # Get the default LLM provider for synthesis
        providers = self._registry.get_all("llm")
        if not providers:
            logger.error("No LLM providers registered -- cannot synthesize")
            memo = InvestmentMemo(
                executive_summary="No LLM provider available for synthesis.",
                agents_used=[o.agent_name for o in agent_outputs],
            )
            return memo, None

        llm: LLMProvider = providers[0]

        # Build synthesis prompt
        analyses = "\n\n".join(
            f"--- {o.agent_name} (confidence: {o.confidence:.2f}, direction: {o.suggested_direction or 'none'}) ---\n{o.analysis}"
            for o in agent_outputs
        )
        trigger_str = json.dumps(trigger_event.payload)[:500]

        prompt = SYNTHESIS_PROMPT.format(
            agent_analyses=analyses,
            trigger=trigger_str,
        )

        messages = [
            {"role": "system", "content": "You are a Chief Investment Officer."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await llm.complete(messages)
            return self._parse_synthesis(
                response, agent_outputs, llm.name, tc
            )
        except Exception:
            logger.exception("Synthesis failed")
            memo = InvestmentMemo(
                executive_summary="Synthesis failed. Agent analyses available in raw form.",
                catalyst=analyses,
                agents_used=[o.agent_name for o in agent_outputs],
                model_provider=llm.name,
            )
            return memo, None

    def _parse_synthesis(
        self,
        response: str,
        agent_outputs: list[AgentOutput],
        provider_name: str,
        tc: TimeContext,
    ) -> tuple[InvestmentMemo, Signal | None]:
        """Parse the synthesis response into a memo and signal."""
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1])
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Could not parse synthesis as JSON, using raw text")
            memo = InvestmentMemo(
                executive_summary=response[:500],
                agents_used=[o.agent_name for o in agent_outputs],
                model_provider=provider_name,
            )
            return memo, None

        # Build memo
        scenarios = []
        for s in data.get("scenarios", []):
            scenarios.append(Scenario(
                name=s.get("name", ""),
                probability=float(s.get("probability", 0)),
                description=s.get("description", ""),
                target_price=s.get("target_price"),
            ))

        memo = InvestmentMemo(
            correlation_id="",
            executive_summary=data.get("executive_summary", ""),
            catalyst=data.get("catalyst", ""),
            market_context=data.get("market_context", ""),
            pricing_vs_view=data.get("pricing_vs_view", ""),
            scenario_tree=scenarios,
            trade_expression=data.get("trade_expression", ""),
            entry_plan=data.get("entry_plan", ""),
            risks=data.get("risks", []),
            monitoring_plan=data.get("monitoring_plan", ""),
            agents_used=[o.agent_name for o in agent_outputs],
            model_provider=provider_name,
        )

        # Build signal if present
        signal = None
        sig_data = data.get("signal")
        if sig_data and sig_data.get("direction") != "hold":
            signal = Signal(
                ticker=sig_data.get("ticker", ""),
                direction=sig_data.get("direction", "hold"),
                catalyst=data.get("catalyst", ""),
                confidence=float(sig_data.get("confidence", 0)),
                entry_target=sig_data.get("entry_target"),
                stop_loss=sig_data.get("stop_loss"),
                take_profit=sig_data.get("take_profit"),
                horizon=sig_data.get("horizon", ""),
            )

        return memo, signal

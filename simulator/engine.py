"""Simulation engine -- replays historical events through the pipeline.

Reuses the exact same AI agents, risk rules, and orchestrator.
The only differences from production:
1. TimeContext filters all data queries to the simulated date
2. Output adapters are mocked (signals captured, not sent)
3. Events come from historical replay, not live sources
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.events import Event, EventTypes
from core.models.signals import Position, Signal
from core.models.simulations import PerformanceMetrics, SimulationConfig, SimulationRun
from core.registry import PluginRegistry
from core.time_context import TimeContext
from engine.memory import MemoryRetriever
from engine.orchestrator import Orchestrator
from risk.engine import RiskEngine
from risk.portfolio import PortfolioTracker
from simulator.metrics import Trade, calculate_metrics
from simulator.mocks import MockOutputAdapter

logger = logging.getLogger(__name__)


class SimulationEngine:
    """Replays historical events through the pipeline in sandbox mode.

    Usage:
        sim = SimulationEngine(config, store, registry)
        run = await sim.run_simulation(sim_config)
        print(run.metrics)
    """

    def __init__(
        self,
        store: Store,
        registry: PluginRegistry,
    ) -> None:
        self._store = store
        self._registry = registry

    async def run_simulation(self, config: SimulationConfig) -> SimulationRun:
        """Execute a full simulation run.

        1. Create a sandboxed environment
        2. Load historical events for the date range
        3. Replay each event through the pipeline
        4. Collect results and calculate metrics
        """
        run = SimulationRun(
            name=f"sim_{config.ai_provider}_{config.date_range[0]}_{config.date_range[1]}",
            config=config,
        )
        run.mark_started()

        logger.info(
            "Starting simulation: %s (%s to %s, provider=%s)",
            run.name, config.date_range[0], config.date_range[1], config.ai_provider,
        )

        try:
            # Set up sandboxed environment
            sim_dir = self._store._home / "simulations" / run.id
            sim_dir.mkdir(parents=True, exist_ok=True)

            # Create sandboxed components
            sim_store = Store(sim_dir)
            sim_bus = AsyncIOBus(events_dir=sim_dir / "events")
            sim_portfolio = PortfolioTracker(sim_store)
            sim_memory = MemoryRetriever(sim_store)

            # Mock output adapter (captures signals, doesn't send them)
            mock_output = MockOutputAdapter(sim_dir / "signals")
            self._registry.register("output", mock_output)

            # Set up risk engine in sandbox
            risk_engine = RiskEngine(
                bus=sim_bus,
                store=sim_store,
                registry=self._registry,
                portfolio=sim_portfolio,
            )

            # Set up orchestrator in sandbox
            orchestrator = Orchestrator(
                bus=sim_bus,
                store=sim_store,
                registry=self._registry,
                portfolio=sim_portfolio,
                memory_retriever=sim_memory,
            )

            # Load historical events
            start_date = datetime.strptime(config.date_range[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_date = datetime.strptime(config.date_range[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)

            # Sync market data for the simulation period
            await self._sync_historical_data(sim_store, config, start_date, end_date)

            # Generate synthetic events (e.g., daily market close events)
            events = self._generate_daily_events(start_date, end_date)

            # Replay events
            signal_count = 0
            for event in events:
                tc = TimeContext.at(event.timestamp, run.id)

                try:
                    memo, signal = await orchestrator.analyze(event, time_context=tc)
                    if signal and signal.direction != "hold":
                        signal_count += 1
                except Exception:
                    logger.debug("Event replay failed for %s", event.timestamp.date())
                    continue

            # Calculate metrics from collected signals
            trades = self._collect_trades(sim_store)
            metrics_dict = calculate_metrics(
                trades,
                initial_capital=config.initial_capital,
            )

            # Calculate benchmark comparison
            metrics_dict.update(
                await self._calculate_benchmarks(start_date, end_date)
            )

            metrics = PerformanceMetrics(**metrics_dict)
            run.mark_completed(metrics=metrics, signal_count=signal_count)

            # Save results
            self._store.write_json(
                f"simulations/{run.id}",
                "results.json",
                run,
            )

            logger.info(
                "Simulation complete: %s | %d signals | Sharpe: %.2f | Return: %.1f%%",
                run.name, signal_count, metrics.sharpe_ratio, metrics.total_return * 100,
            )

            # Clean up sandbox store
            sim_store.close()

        except Exception as e:
            logger.exception("Simulation failed")
            run.mark_failed(str(e))
            self._store.write_json(
                f"simulations/{run.id}",
                "results.json",
                run,
            )

        return run

    async def _sync_historical_data(
        self,
        sim_store: Store,
        config: SimulationConfig,
        start: datetime,
        end: datetime,
    ) -> None:
        """Load historical market data into the simulation's SQLite."""
        providers = self._registry.get_all("market_data")

        for provider in providers:
            try:
                # Fetch data for all configured tickers
                data = await provider.fetch(
                    tickers=[],  # provider uses its configured tickers
                    start=start - timedelta(days=30),  # extra lookback for context
                    end=end,
                )
                if data:
                    sim_store.save_market_data(data)
                    logger.info(
                        "Loaded %d records from %s for simulation",
                        len(data), provider.name,
                    )
            except Exception:
                logger.exception("Failed to load data from %s", provider.name)

    def _generate_daily_events(
        self,
        start: datetime,
        end: datetime,
    ) -> list[Event]:
        """Generate daily market-close events for the simulation period.

        In a real implementation, this would also load historical
        macro events (CPI releases, FOMC, earnings, etc.).
        """
        events = []
        current = start

        while current <= end:
            # Skip weekends (simple approximation)
            if current.weekday() < 5:
                event = Event(
                    type=EventTypes.SCHEDULE_FIRED,
                    timestamp=current.replace(hour=16, minute=0),
                    source="simulator",
                    payload={
                        "task_name": "Daily market analysis",
                        "handler": "analysis.daily",
                        "simulated": True,
                    },
                )
                events.append(event)

            current += timedelta(days=1)

        logger.info("Generated %d daily events for simulation", len(events))
        return events

    def _collect_trades(self, sim_store: Store) -> list[Trade]:
        """Collect completed trades from the simulation's AI portfolio."""
        positions = sim_store.list_json("positions/ai", Position)

        trades = []
        for pos in positions:
            if pos.status == "closed" and pos.close_price is not None:
                pnl = pos.realized_pnl or 0
                pnl_pct = pos.realized_pnl_percent or 0
                holding_days = 1
                if pos.closed_at and pos.opened_at:
                    holding_days = max(1, (pos.closed_at - pos.opened_at).days)

                trades.append(Trade(
                    ticker=pos.ticker,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    exit_price=pos.close_price,
                    size=pos.size or 1,
                    pnl=pnl,
                    pnl_percent=pnl_pct,
                    holding_days=holding_days,
                ))

        return trades

    async def _calculate_benchmarks(
        self,
        start: datetime,
        end: datetime,
    ) -> dict:
        """Calculate benchmark returns (SPY, QQQ) for comparison."""
        benchmarks = {"vs_spy": 0.0, "vs_qqq": 0.0}

        for ticker, key in [("SPY", "vs_spy"), ("QQQ", "vs_qqq")]:
            start_price = self._store.get_latest_price(ticker, as_of=start)
            end_price = self._store.get_latest_price(ticker, as_of=end)

            if start_price and end_price:
                benchmarks[key] = (end_price - start_price) / start_price

        return benchmarks

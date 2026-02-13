"""OpenSuperFin entrypoint -- wires all components together and starts the server.

Usage:
    python main.py
    python main.py --config /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import web

from core.bus import AsyncIOBus
from core.config import load_config
from core.data.store import Store
from core.registry import PluginRegistry
from engine.interface import AIInterface
from risk.portfolio import PortfolioTracker
from scheduler.runner import Scheduler
from server import create_app


def setup_logging(level: str) -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet down noisy libraries
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenSuperFin trading advisory system")
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to config.yaml (default: ~/.opensuperfin/config.yaml)",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        help="Path to .env file (default: ~/.opensuperfin/.env)",
    )
    return parser.parse_args()


def _days_from_period(period: str) -> int:
    """Parse a period string like '7d' into an integer day count."""
    digits = "".join(ch for ch in period if ch.isdigit())
    return int(digits) if digits else 7


async def _load_plugins(config, bus, store, registry, ai_interface: AIInterface) -> None:
    """Load and register all plugins from config."""
    logger = logging.getLogger("opensuperfin.plugins")

    # 1. Load AI providers
    for provider_name, provider_config in config.ai.providers.items():
        # Provider config is a Pydantic model (not dict) and has no "enabled" flag.
        # We consider it configured when an API key is present.
        if not provider_config.api_key:
            continue
        try:
            if provider_name == "openai":
                from plugins.ai_providers.openai import OpenAIProvider
                instance = OpenAIProvider(
                    api_key=provider_config.api_key,
                    model=provider_config.model or "gpt-4o",
                    temperature=provider_config.temperature,
                )
                registry.register("llm", instance)
                logger.info("Loaded AI provider: %s", provider_name)
            elif provider_name == "anthropic":
                from plugins.ai_providers.anthropic import AnthropicProvider
                instance = AnthropicProvider(
                    api_key=provider_config.api_key,
                    model=provider_config.model or "claude-sonnet-4-20250514",
                    temperature=provider_config.temperature,
                )
                registry.register("llm", instance)
                logger.info("Loaded AI provider: %s", provider_name)
            elif provider_name == "openrouter":
                from plugins.ai_providers.openrouter import OpenRouterProvider
                instance = OpenRouterProvider(
                    api_key=provider_config.api_key,
                    model=provider_config.model or "openai/gpt-4o",
                    temperature=provider_config.temperature,
                )
                registry.register("llm", instance)
                logger.info("Loaded AI provider: %s", provider_name)
        except Exception as e:
            logger.error("Failed to load AI provider %s: %s", provider_name, e)

    # 2. Load agents
    for agent_name, agent_config in config.ai.agents.items():
        if not agent_config.get("enabled", False):
            continue
        try:
            if agent_name == "macro":
                from plugins.agents.macro import MacroStrategist
                # Get the default provider instance
                default_provider = config.ai.default_provider
                try:
                    llm = registry.get("llm", default_provider)
                except KeyError:
                    logger.warning(
                        "Agent %s requires LLM provider %s, but it's not loaded",
                        agent_name, default_provider,
                    )
                    continue
                instance = MacroStrategist(llm=llm)
                registry.register("agent", instance)
                logger.info("Loaded agent: %s", agent_name)
        except Exception as e:
            logger.error("Failed to load agent %s: %s", agent_name, e)

    # 3. Load market data providers
    for provider_name, provider_config in config.market_data.providers.items():
        if not provider_config.enabled:
            continue
        try:
            if provider_name == "yahoo_finance":
                from plugins.market_data.yahoo_finance import YahooFinanceProvider
                instance = YahooFinanceProvider(
                    tickers=provider_config.tickers,
                )
                registry.register("market_data", instance)
                logger.info("Loaded market data provider: %s", provider_name)
        except Exception as e:
            logger.error("Failed to load market data provider %s: %s", provider_name, e)

    # 4. Load risk rules
    try:
        from plugins.risk_rules.confidence import ConfidenceRule
        from plugins.risk_rules.concentration import ConcentrationRule
        from plugins.risk_rules.frequency import FrequencyRule
        from plugins.risk_rules.drawdown import DrawdownRule

        confidence_cfg = config.risk.rules.confidence
        concentration_cfg = config.risk.rules.concentration
        frequency_cfg = config.risk.rules.frequency
        drawdown_cfg = config.risk.rules.drawdown

        # Register risk rules
        registry.register(
            "risk_rule",
            ConfidenceRule(min_confidence=float(confidence_cfg.get("min_confidence", 0.6))),
        )
        registry.register(
            "risk_rule",
            ConcentrationRule(
                max_single_position=float(concentration_cfg.get("max_single_position", 0.15)),
                max_sector_exposure=float(concentration_cfg.get("max_sector_exposure", 0.30)),
            ),
        )
        registry.register(
            "risk_rule",
            FrequencyRule(
                max_signals_per_day=int(frequency_cfg.get("max_signals_per_day", 5)),
                events_dir=config.home_path / "events",
            ),
        )
        registry.register(
            "risk_rule",
            DrawdownRule(max_portfolio_drawdown=float(drawdown_cfg.get("max_portfolio_drawdown", 0.15))),
        )
        logger.info("Loaded 4 risk rules")
    except Exception as e:
        logger.error("Failed to load risk rules: %s", e)

    # 5. Load task handlers
    try:
        from plugins.task_handlers.comparison import ComparisonHandler

        handler = ComparisonHandler(
            store=store,
            bus=bus,
            registry=registry,
            min_outcome_days=_days_from_period(config.learning.min_outcome_period),
        )
        registry.register("task_handler", handler)
        logger.info("Loaded task handler: %s", handler.name)
    except Exception as e:
        logger.error("Failed to load task handlers: %s", e)

    # 6. Load integrations (and start them)
    for integration_name, integration_config in config.integrations.items():
        if not integration_config.get("enabled", False):
            continue
        try:
            if integration_name == "telegram":
                from plugins.integrations.telegram import TelegramIntegration

                instance = TelegramIntegration(
                    bot_token=integration_config["bot_token"],
                    channels=integration_config.get("channels", []),
                )

                async def _handle_message(payload: dict, tg: TelegramIntegration = instance) -> None:
                    text = (payload.get("text") or "").strip()
                    if not text:
                        return

                    channel_id = payload.get("channel_id") or payload.get("chat_id") or "default"
                    response = await ai_interface.handle_message(
                        text=text,
                        channel_id=channel_id,
                        source="telegram",
                    )
                    if response:
                        await tg.send_text(response, channel_id=channel_id)

                instance.on_message(_handle_message)
                registry.register("input", instance)
                registry.register("output", instance)

                # Start the integration (begins polling)
                await instance.start()
                logger.info("Loaded and started integration: %s", integration_name)
        except Exception as e:
            logger.error("Failed to load integration %s: %s", integration_name, e)


async def run(config_path: str | None = None, env_path: str | None = None) -> None:
    """Initialize all components and start the server."""
    # Load configuration
    config = load_config(config_path=config_path, env_path=env_path)
    logger = logging.getLogger("opensuperfin")
    logger.info("Configuration loaded from %s", config.home_path)

    # Initialize core infrastructure
    store = Store(config.home_path)
    bus = AsyncIOBus(events_dir=config.home_path / "events")
    registry = PluginRegistry()

    # Parse check interval from config (e.g., "60s" -> 60)
    check_interval_str = config.scheduler.check_interval
    check_interval = int(check_interval_str.rstrip("s"))

    # Initialize scheduler
    scheduler = Scheduler(
        store=store,
        bus=bus,
        registry=registry,
        check_interval=check_interval,
    )
    portfolio = PortfolioTracker(store=store)
    ai_interface = AIInterface(
        registry=registry,
        store=store,
        bus=bus,
        portfolio=portfolio,
        scheduler=scheduler,
    )

    # Load and register plugins based on config
    await _load_plugins(config, bus, store, registry, ai_interface)
    logger.info("Plugin registry: %s", registry.summary())

    # Create HTTP server
    app = create_app(
        config=config,
        bus=bus,
        store=store,
        registry=registry,
        scheduler=scheduler,
    )

    # Start scheduler
    await scheduler.start()

    # Start server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.server.host, config.server.port)
    await site.start()

    logger.info(
        "OpenSuperFin running at http://%s:%d",
        config.server.host,
        config.server.port,
    )
    logger.info("State directory: %s", config.home_path)

    # Run until interrupted
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("Shutting down...")
        await scheduler.stop()

        # Stop all input integrations (dedupe because adapters can implement input+output)
        stopped: set[int] = set()
        for integration in registry.get_all("input"):
            key = id(integration)
            if key in stopped:
                continue
            stopped.add(key)
            try:
                if hasattr(integration, "stop"):
                    await integration.stop()
                    logger.info("Stopped integration: %s", integration.name)
            except Exception as e:
                logger.error("Error stopping integration %s: %s", getattr(integration, "name", "?"), e)

        # Close all providers
        for provider in registry.get_all("llm"):
            try:
                if hasattr(provider, "close"):
                    await provider.close()
            except Exception as e:
                logger.error("Error closing LLM provider %s: %s", getattr(provider, "name", "?"), e)

        store.close()
        await runner.cleanup()
        logger.info("Shutdown complete")


def main() -> None:
    args = parse_args()
    setup_logging("INFO")
    try:
        asyncio.run(run(config_path=args.config, env_path=args.env))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

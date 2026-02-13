"""OpenSuperFin entrypoint -- wires all components together and starts the server.

Usage:
    python main.py
    python main.py --config /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from aiohttp import web

from core.bus import AsyncIOBus
from core.config import load_config
from core.data.store import Store
from core.registry import PluginRegistry
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

    # TODO: Load and register plugins based on config
    # This is where market data providers, integrations, AI providers,
    # agents, risk rules, and task handlers get loaded and registered.
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

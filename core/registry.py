"""Plugin registry -- discovers, stores, and retrieves protocol implementations.

At startup, the system instantiates plugins based on config.yaml and registers
them here. Core components query the registry for implementations by protocol type.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from core.protocols import (
    AIAgent,
    EventBus,
    InputAdapter,
    LLMProvider,
    MarketDataProvider,
    OutputAdapter,
    RiskRule,
    TaskHandler,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# All supported protocol types
PROTOCOL_TYPES = {
    "event_bus": EventBus,
    "market_data": MarketDataProvider,
    "input": InputAdapter,
    "output": OutputAdapter,
    "llm": LLMProvider,
    "agent": AIAgent,
    "risk_rule": RiskRule,
    "task_handler": TaskHandler,
}


class PluginRegistry:
    """Central registry for all protocol implementations.

    Usage:
        registry = PluginRegistry()
        registry.register("market_data", yahoo_finance_provider)
        registry.register("market_data", coingecko_provider)

        providers = registry.get_all("market_data")  # [yahoo, coingecko]
        yahoo = registry.get("market_data", "yahoo_finance")
    """

    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Any]] = {key: {} for key in PROTOCOL_TYPES}

    def register(self, protocol_key: str, instance: Any) -> None:
        """Register a plugin instance under a protocol type.

        The instance must have a `name` property.
        """
        if protocol_key not in PROTOCOL_TYPES:
            raise ValueError(
                f"Unknown protocol key '{protocol_key}'. "
                f"Must be one of: {list(PROTOCOL_TYPES.keys())}"
            )

        name = instance.name
        if name in self._plugins[protocol_key]:
            logger.warning(
                "Overwriting existing %s plugin '%s'", protocol_key, name
            )

        self._plugins[protocol_key][name] = instance
        logger.info("Registered %s plugin: %s", protocol_key, name)

    def get(self, protocol_key: str, name: str) -> Any:
        """Get a specific plugin by protocol type and name.

        Raises KeyError if not found.
        """
        if protocol_key not in self._plugins:
            raise KeyError(f"Unknown protocol key: {protocol_key}")
        if name not in self._plugins[protocol_key]:
            available = list(self._plugins[protocol_key].keys())
            raise KeyError(
                f"No {protocol_key} plugin named '{name}'. "
                f"Available: {available}"
            )
        return self._plugins[protocol_key][name]

    def get_all(self, protocol_key: str) -> list[Any]:
        """Get all plugins registered for a protocol type."""
        if protocol_key not in self._plugins:
            raise KeyError(f"Unknown protocol key: {protocol_key}")
        return list(self._plugins[protocol_key].values())

    def has(self, protocol_key: str, name: str) -> bool:
        """Check if a plugin is registered."""
        return (
            protocol_key in self._plugins
            and name in self._plugins[protocol_key]
        )

    def names(self, protocol_key: str) -> list[str]:
        """List all registered plugin names for a protocol type."""
        if protocol_key not in self._plugins:
            return []
        return list(self._plugins[protocol_key].keys())

    def summary(self) -> dict[str, list[str]]:
        """Return a summary of all registered plugins."""
        return {
            key: list(plugins.keys())
            for key, plugins in self._plugins.items()
            if plugins
        }

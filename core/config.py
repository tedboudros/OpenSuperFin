"""Configuration loader -- reads config.yaml + .env, validates with Pydantic.

Resolves ${ENV_VAR} references in YAML values from environment variables.
Fails fast with clear errors if required config is missing.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default home directory for all state files
DEFAULT_HOME = Path.home() / ".opensuperfin"


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}")
        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                logger.warning("Environment variable %s not set", var_name)
                return match.group(0)  # leave unresolved
            return env_value
        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Config models (Pydantic)
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8321


class MarketDataProviderConfig(BaseModel):
    enabled: bool = True
    tickers: list[str] = Field(default_factory=list)
    # Provider-specific extra settings
    extra: dict = Field(default_factory=dict)


class MarketDataConfig(BaseModel):
    poll_interval: str = "5m"
    history_depth: str = "2y"
    providers: dict[str, MarketDataProviderConfig] = Field(default_factory=dict)


class RiskRulesConfig(BaseModel):
    confidence: dict = Field(default_factory=lambda: {"min_confidence": 0.6})
    concentration: dict = Field(default_factory=lambda: {
        "max_single_position": 0.15,
        "max_sector_exposure": 0.30,
    })
    frequency: dict = Field(default_factory=lambda: {"max_signals_per_day": 5})
    drawdown: dict = Field(default_factory=lambda: {"max_portfolio_drawdown": 0.15})


class RiskConfig(BaseModel):
    rules: RiskRulesConfig = Field(default_factory=RiskRulesConfig)


class PositionTrackingConfig(BaseModel):
    confirmation_timeout: str = "4h"
    allow_user_initiated: bool = True


class AIProviderConfig(BaseModel):
    api_key: str = ""
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.3


class AIConfig(BaseModel):
    default_provider: str = "anthropic"
    providers: dict[str, AIProviderConfig] = Field(default_factory=dict)
    task_routing: dict[str, str] = Field(default_factory=dict)
    agents: dict[str, dict] = Field(default_factory=dict)


class LearningConfig(BaseModel):
    comparison_schedule: str = "0 9 * * 0"
    min_outcome_period: str = "7d"
    max_memories_in_context: int = 10
    memory_relevance_window: str = "90d"


class SchedulerConfig(BaseModel):
    timezone: str = "America/New_York"
    check_interval: str = "60s"
    default_tasks: list[dict] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    audit_events: bool = True
    llm_calls: bool = True


class AppConfig(BaseModel):
    """Top-level application configuration."""

    home_dir: str = str(DEFAULT_HOME)
    server: ServerConfig = Field(default_factory=ServerConfig)
    integrations: dict[str, Any] = Field(default_factory=dict)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    position_tracking: PositionTrackingConfig = Field(default_factory=PositionTrackingConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def home_path(self) -> Path:
        """Resolved home directory as a Path."""
        return Path(self.home_dir).expanduser()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_config(
    config_path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> AppConfig:
    """Load configuration from YAML + .env files.

    1. Load .env into environment variables
    2. Load config.yaml and resolve ${ENV_VAR} references
    3. Validate against Pydantic models
    4. Create home directory structure if needed
    """
    # Determine paths
    home = Path(os.environ.get("OPENSUPERFIN_HOME", str(DEFAULT_HOME))).expanduser()

    if env_path is None:
        env_path = home / ".env"
    if config_path is None:
        config_path = home / "config.yaml"

    env_path = Path(env_path)
    config_path = Path(config_path)

    # Load .env
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded environment from %s", env_path)
    else:
        logger.debug("No .env file at %s", env_path)

    # Load config.yaml
    raw_config: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            raw_config = yaml.safe_load(f) or {}
        logger.info("Loaded config from %s", config_path)
    else:
        logger.warning("No config file at %s, using defaults", config_path)

    # Resolve ${ENV_VAR} references
    resolved = _resolve_env_vars(raw_config)

    # Override home_dir if set via env
    if "OPENSUPERFIN_HOME" in os.environ:
        resolved["home_dir"] = os.environ["OPENSUPERFIN_HOME"]

    # Validate
    config = AppConfig(**resolved)

    # Ensure home directory structure exists
    _ensure_directories(config.home_path)

    return config


def _ensure_directories(home: Path) -> None:
    """Create the state directory structure if it doesn't exist."""
    dirs = [
        home,
        home / "events",
        home / "memos",
        home / "signals",
        home / "positions" / "ai",
        home / "positions" / "human",
        home / "memories",
        home / "tasks",
        home / "market",
        home / "simulations",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

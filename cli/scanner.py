"""Plugin scanner -- auto-discovers PLUGIN_META from the plugins/ directory.

Walks all Python files under plugins/, imports them, and collects any
module-level PLUGIN_META dicts. This is what makes the system fully
dynamic: add a new plugin file with PLUGIN_META and it automatically
appears in the setup wizard and CLI.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Categories in the order they should be presented to the user
CATEGORY_ORDER = [
    "ai_provider",
    "market_data",
    "integration",
    "agent",
    "risk_rule",
    "task_handler",
]

CATEGORY_LABELS = {
    "ai_provider": "AI Providers",
    "market_data": "Market Data Sources",
    "integration": "Integrations",
    "agent": "AI Agents",
    "risk_rule": "Risk Rules",
    "task_handler": "Task Handlers",
}


@dataclass
class ConfigField:
    """A single configuration field for a plugin."""

    key: str
    label: str
    type: str  # secret | string | number | boolean | choice | list
    required: bool = False
    default: Any = None
    description: str = ""
    placeholder: str = ""
    env_var: str | None = None
    choices: list[str] = field(default_factory=list)


@dataclass
class PluginInfo:
    """Discovered plugin metadata."""

    name: str
    display_name: str
    description: str
    category: str
    protocols: list[str]
    class_name: str
    module_path: str  # e.g., "plugins.integrations.telegram"
    pip_dependencies: list[str]
    setup_instructions: str
    config_fields: list[ConfigField]

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def has_config(self) -> bool:
        return len(self.config_fields) > 0

    @property
    def has_secrets(self) -> bool:
        return any(f.type == "secret" for f in self.config_fields)

    @property
    def choice_label(self) -> str:
        """Label shown in the setup wizard checkbox list."""
        return f"{self.display_name} -- {self.description}"


def discover_plugins(plugins_dir: Path | None = None) -> dict[str, list[PluginInfo]]:
    """Scan the plugins directory and collect all PLUGIN_META.

    Returns a dict of category -> list of PluginInfo, in presentation order.
    """
    if plugins_dir is None:
        plugins_dir = Path(__file__).parent.parent / "plugins"

    results: dict[str, list[PluginInfo]] = {cat: [] for cat in CATEGORY_ORDER}

    # Walk all subdirectories of plugins/
    for subdir in sorted(plugins_dir.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("_"):
            continue

        for filepath in sorted(subdir.glob("*.py")):
            if filepath.name.startswith("_"):
                continue

            plugin = _load_plugin_meta(filepath, plugins_dir)
            if plugin:
                category = plugin.category
                if category not in results:
                    results[category] = []
                results[category].append(plugin)

    # Remove empty categories
    return {k: v for k, v in results.items() if v}


def _load_plugin_meta(filepath: Path, plugins_root: Path) -> PluginInfo | None:
    """Import a plugin file and extract its PLUGIN_META."""
    # Build module path: plugins/integrations/telegram.py -> plugins.integrations.telegram
    relative = filepath.relative_to(plugins_root.parent)
    module_path = str(relative.with_suffix("")).replace("/", ".").replace("\\", ".")

    try:
        module = importlib.import_module(module_path)
    except Exception:
        logger.debug("Could not import %s", module_path)
        return None

    meta = getattr(module, "PLUGIN_META", None)
    if not meta or not isinstance(meta, dict):
        return None

    # Parse config fields
    config_fields = []
    for f in meta.get("config_fields", []):
        config_fields.append(ConfigField(
            key=f.get("key", ""),
            label=f.get("label", f.get("key", "")),
            type=f.get("type", "string"),
            required=f.get("required", False),
            default=f.get("default"),
            description=f.get("description", ""),
            placeholder=f.get("placeholder", ""),
            env_var=f.get("env_var"),
            choices=f.get("choices", []),
        ))

    return PluginInfo(
        name=meta.get("name", filepath.stem),
        display_name=meta.get("display_name", meta.get("name", filepath.stem)),
        description=meta.get("description", ""),
        category=meta.get("category", "unknown"),
        protocols=meta.get("protocols", []),
        class_name=meta.get("class_name", ""),
        module_path=module_path,
        pip_dependencies=meta.get("pip_dependencies", []),
        setup_instructions=meta.get("setup_instructions", ""),
        config_fields=config_fields,
    )


def list_all_plugins(plugins_dir: Path | None = None) -> list[PluginInfo]:
    """Flat list of all discovered plugins."""
    by_category = discover_plugins(plugins_dir)
    return [plugin for plugins in by_category.values() for plugin in plugins]


def get_plugin(name: str, plugins_dir: Path | None = None) -> PluginInfo | None:
    """Find a specific plugin by name."""
    for plugin in list_all_plugins(plugins_dir):
        if plugin.name == name:
            return plugin
    return None

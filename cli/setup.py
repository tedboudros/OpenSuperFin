"""Interactive setup wizard -- guides the user through configuration.

Uses questionary for arrow-key navigation, checkboxes, and text input.
Fully dynamic: auto-discovers plugins via PLUGIN_META. Adding a new
plugin file automatically makes it appear in the wizard.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import questionary
from questionary import Choice

from cli.banner import print_banner
from cli.config_gen import generate_config
from cli.scanner import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    ConfigField,
    PluginInfo,
    discover_plugins,
)

# Questionary style
STYLE = questionary.Style([
    ("qmark", "fg:cyan bold"),
    ("question", "fg:white bold"),
    ("answer", "fg:green"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:green"),
    ("instruction", "fg:gray italic"),
])


def run_setup(home_dir: Path | None = None) -> None:
    """Run the full interactive setup wizard."""
    print_banner()
    print("  Welcome to OpenSuperFin setup!\n")

    # Step 1: Home directory
    if home_dir is None:
        default_home = str(Path.home() / ".opensuperfin")
        home_str = questionary.text(
            "Where should OpenSuperFin store its data?",
            default=default_home,
            style=STYLE,
        ).ask()
        if home_str is None:
            _abort()
        home_dir = Path(home_str).expanduser()

    # Step 2: Discover all available plugins
    all_plugins = discover_plugins()

    # Step 3: Let user select plugins by category
    enabled_plugins: list[PluginInfo] = []
    plugin_values: dict[str, dict[str, Any]] = {}

    # Categories that require user selection (checkbox)
    selectable = ["ai_provider", "market_data", "integration"]
    # Categories enabled by default (agents, risk rules, task handlers)
    auto_enabled = ["agent", "risk_rule", "task_handler"]

    for category in CATEGORY_ORDER:
        plugins = all_plugins.get(category, [])
        if not plugins:
            continue

        if category in selectable:
            selected = _select_plugins(category, plugins)
            enabled_plugins.extend(selected)
        elif category in auto_enabled:
            # Auto-enable, but still let user configure
            enabled_plugins.extend(plugins)

    # Step 4: Configure each selected plugin
    print()
    for plugin in enabled_plugins:
        if not plugin.has_config:
            continue
        values = _configure_plugin(plugin)
        if values is not None:
            plugin_values[plugin.name] = values

    # Step 5: Generate config files
    print()
    print("  Writing configuration...")
    config_path, env_path = generate_config(
        home_dir=home_dir,
        enabled_plugins=enabled_plugins,
        plugin_values=plugin_values,
    )

    # Step 6: Install extra pip dependencies if any
    extra_deps = set()
    for plugin in enabled_plugins:
        extra_deps.update(plugin.pip_dependencies)
    if extra_deps:
        print(f"  Installing plugin dependencies: {', '.join(extra_deps)}")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *extra_deps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Done
    print()
    print(f"  Config:   {config_path}")
    print(f"  Secrets:  {env_path}")
    print()
    print("  OpenSuperFin is ready! Run:")
    print("    opensuperfin start")
    print()


def run_plugin_setup(plugin_name: str) -> None:
    """Configure a single plugin interactively."""
    from cli.scanner import get_plugin

    plugin = get_plugin(plugin_name)
    if not plugin:
        print(f"  Unknown plugin: {plugin_name}")
        print(f"  Run 'opensuperfin plugin list' to see available plugins.")
        sys.exit(1)

    if not plugin.has_config:
        print(f"  {plugin.display_name} has no configuration options.")
        return

    print_banner()
    print(f"  Configuring: {plugin.display_name}\n")

    values = _configure_plugin(plugin)
    if values:
        print(f"\n  Configuration for {plugin.display_name}:")
        for k, v in values.items():
            display_v = "********" if any(f.key == k and f.type == "secret" for f in plugin.config_fields) else v
            print(f"    {k}: {display_v}")
        print(f"\n  Run 'opensuperfin config' to apply changes to your configuration.\n")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _select_plugins(category: str, plugins: list[PluginInfo]) -> list[PluginInfo]:
    """Show a checkbox list for selecting plugins in a category."""
    label = CATEGORY_LABELS.get(category, category)
    required = category == "ai_provider"

    choices = [
        Choice(
            title=p.choice_label,
            value=p,
            checked=(category in ("market_data",)),  # auto-check market data
        )
        for p in plugins
    ]

    while True:
        selected = questionary.checkbox(
            f"Select {label}:",
            choices=choices,
            style=STYLE,
            instruction="(use SPACE to select, ENTER to confirm)",
        ).ask()

        if selected is None:
            _abort()

        if required and not selected:
            print("  Please select at least one. Use SPACE to toggle selection, then ENTER.")
            continue

        return selected  # type: ignore[return-value]


def _configure_plugin(plugin: PluginInfo) -> dict[str, Any] | None:
    """Walk through a plugin's config fields and collect values."""
    if not plugin.config_fields:
        return {}

    # Show setup instructions if present
    instructions = plugin.setup_instructions.strip()
    if instructions:
        print(f"\n  --- {plugin.display_name} Setup ---")
        for line in instructions.split("\n"):
            print(f"  {line}")
        print()

    values: dict[str, Any] = {}

    for field in plugin.config_fields:
        value = _prompt_field(field, plugin.display_name)
        if value is not None:
            values[field.key] = value

    return values


def _prompt_field(field: ConfigField, plugin_name: str) -> Any:
    """Prompt for a single config field based on its type."""
    label = field.label
    if field.description:
        label = f"{field.label} ({field.description})"

    default = field.default

    match field.type:
        case "secret":
            value = questionary.password(
                f"{field.label}:",
                style=STYLE,
            ).ask()
            if value is None:
                _abort()
            return value

        case "choice":
            value = questionary.select(
                f"{field.label}:",
                choices=field.choices,
                default=default,
                style=STYLE,
            ).ask()
            if value is None:
                _abort()
            return value

        case "boolean":
            value = questionary.confirm(
                f"{field.label}?",
                default=bool(default) if default is not None else True,
                style=STYLE,
            ).ask()
            if value is None:
                _abort()
            return value

        case "number":
            default_str = str(default) if default is not None else ""
            value = questionary.text(
                f"{field.label}:",
                default=default_str,
                style=STYLE,
            ).ask()
            if value is None:
                _abort()
            try:
                num = float(value)
                return int(num) if num == int(num) else num
            except ValueError:
                return default

        case "list":
            default_str = ", ".join(default) if isinstance(default, list) else str(default or "")
            value = questionary.text(
                f"{field.label} (comma-separated):",
                default=default_str,
                style=STYLE,
            ).ask()
            if value is None:
                _abort()
            return [item.strip() for item in value.split(",") if item.strip()]

        case _:  # string
            default_str = str(default) if default is not None else ""
            value = questionary.text(
                f"{field.label}:",
                default=default_str,
                style=STYLE,
            ).ask()
            if value is None:
                _abort()
            return value


def _abort() -> None:
    """User pressed Ctrl+C or cancelled."""
    print("\n  Setup cancelled.\n")
    sys.exit(0)

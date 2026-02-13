"""OpenSuperFin CLI -- the `opensuperfin` command.

Usage:
    opensuperfin setup              Full interactive setup wizard
    opensuperfin start              Start the server
    opensuperfin status             Show system status
    opensuperfin config             Re-run the configuration wizard
    opensuperfin plugin list        List all available plugins
    opensuperfin plugin <name>      Configure a specific plugin
    opensuperfin plugin enable <n>  Enable a plugin
    opensuperfin plugin disable <n> Disable a plugin
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def get_home_dir() -> Path:
    """Get the OpenSuperFin home directory."""
    return Path(os.environ.get("OPENSUPERFIN_HOME", Path.home() / ".opensuperfin")).expanduser()


def cmd_setup(args: argparse.Namespace) -> None:
    """Run the interactive setup wizard."""
    from cli.setup import run_setup
    home = get_home_dir() if not args.home else Path(args.home).expanduser()
    run_setup(home_dir=home)


def cmd_start(args: argparse.Namespace) -> None:
    """Start the OpenSuperFin server."""
    from main import run, setup_logging
    setup_logging("INFO")

    config_path = get_home_dir() / "config.yaml"
    if not config_path.exists():
        print("  No configuration found. Run 'opensuperfin setup' first.")
        sys.exit(1)

    try:
        asyncio.run(run(config_path=str(config_path)))
    except KeyboardInterrupt:
        pass


def cmd_status(args: argparse.Namespace) -> None:
    """Show system status."""
    from cli.banner import print_banner
    print_banner()

    home = get_home_dir()
    config_path = home / "config.yaml"

    print(f"  Home:     {home}")
    print(f"  Config:   {config_path} ({'exists' if config_path.exists() else 'NOT FOUND'})")
    print(f"  Database: {home / 'db.sqlite'} ({'exists' if (home / 'db.sqlite').exists() else 'NOT FOUND'})")
    print()

    # Count state files
    dirs = {
        "Signals": home / "signals",
        "Positions (AI)": home / "positions" / "ai",
        "Positions (Human)": home / "positions" / "human",
        "Memories": home / "memories",
        "Tasks": home / "tasks",
        "Memos": home / "memos",
        "Event logs": home / "events",
    }

    for label, path in dirs.items():
        if path.exists():
            count = len(list(path.glob("*.json")) + list(path.glob("*.jsonl")) + list(path.glob("*.md")))
            if count:
                print(f"  {label}: {count} files")

    # Show discovered plugins
    print()
    from cli.scanner import discover_plugins, CATEGORY_LABELS
    plugins = discover_plugins()
    for cat, items in plugins.items():
        names = ", ".join(p.display_name for p in items)
        print(f"  {CATEGORY_LABELS.get(cat, cat)}: {names}")

    print()


def cmd_config(args: argparse.Namespace) -> None:
    """Re-run the configuration wizard."""
    from cli.setup import run_setup
    run_setup(home_dir=get_home_dir())


def cmd_plugin(args: argparse.Namespace) -> None:
    """Plugin management commands."""
    action = args.plugin_action

    if action == "list":
        _plugin_list()
    elif action == "enable":
        _plugin_toggle(args.plugin_name, enable=True)
    elif action == "disable":
        _plugin_toggle(args.plugin_name, enable=False)
    else:
        # Treat as plugin name to configure
        from cli.setup import run_plugin_setup
        run_plugin_setup(action)


def _plugin_list() -> None:
    """List all available plugins."""
    from cli.scanner import discover_plugins, CATEGORY_LABELS

    plugins = discover_plugins()
    print()
    for cat, items in plugins.items():
        print(f"  {CATEGORY_LABELS.get(cat, cat)}:")
        for p in items:
            deps = f" [requires: {', '.join(p.pip_dependencies)}]" if p.pip_dependencies else ""
            fields = f" ({len(p.config_fields)} config fields)" if p.config_fields else ""
            print(f"    {p.name:20s} {p.display_name}{fields}{deps}")
        print()


def _plugin_toggle(name: str | None, enable: bool) -> None:
    """Enable or disable a plugin in config.yaml."""
    if not name:
        print("  Usage: opensuperfin plugin enable <name>")
        return

    import yaml
    home = get_home_dir()
    config_path = home / "config.yaml"

    if not config_path.exists():
        print("  No config file. Run 'opensuperfin setup' first.")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    # Try to find the plugin in various config sections
    sections = ["integrations", "ai"]
    found = False

    for section in sections:
        if section in config:
            sec = config[section]
            if isinstance(sec, dict):
                # Check direct keys
                if name in sec:
                    if isinstance(sec[name], dict):
                        sec[name]["enabled"] = enable
                        found = True
                # Check providers sub-dict
                if "providers" in sec and isinstance(sec["providers"], dict):
                    if name in sec["providers"]:
                        sec["providers"][name]["enabled"] = enable
                        found = True

    if "market_data" in config and "providers" in config["market_data"]:
        if name in config["market_data"]["providers"]:
            config["market_data"]["providers"][name]["enabled"] = enable
            found = True

    if found:
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        action = "Enabled" if enable else "Disabled"
        print(f"  {action}: {name}")
    else:
        print(f"  Plugin '{name}' not found in config. Run 'opensuperfin config' to set it up.")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="opensuperfin",
        description="OpenSuperFin -- Lightweight trading advisory system",
    )
    parser.add_argument("--home", type=str, default=None, help="OpenSuperFin home directory")

    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Run the interactive setup wizard")

    # start
    sub.add_parser("start", help="Start the OpenSuperFin server")

    # status
    sub.add_parser("status", help="Show system status")

    # config
    sub.add_parser("config", help="Re-run the configuration wizard")

    # plugin
    plugin_parser = sub.add_parser("plugin", help="Plugin management")
    plugin_parser.add_argument(
        "plugin_action",
        type=str,
        help="list | enable | disable | <plugin-name>",
    )
    plugin_parser.add_argument(
        "plugin_name",
        type=str,
        nargs="?",
        default=None,
        help="Plugin name (for enable/disable)",
    )

    return parser


def main() -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "setup": cmd_setup,
        "start": cmd_start,
        "status": cmd_status,
        "config": cmd_config,
        "plugin": cmd_plugin,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

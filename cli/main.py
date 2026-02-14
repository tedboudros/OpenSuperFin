"""ClawQuant CLI -- the `clawquant` command.

Usage:
    clawquant setup              Full interactive setup wizard
    clawquant start              Start the server
    clawquant status             Show system status
    clawquant update             Pull latest code from GitHub
    clawquant config             Re-run the configuration wizard
    clawquant plugin list        List all available plugins
    clawquant plugin <name>      Configure a specific plugin
    clawquant plugin enable <n>  Enable a plugin
    clawquant plugin disable <n> Disable a plugin
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path


def get_home_dir() -> Path:
    """Get the ClawQuant home directory."""
    return Path(os.environ.get("CLAWQUANT_HOME", Path.home() / ".clawquant")).expanduser()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _load_update_preferences(config_path: Path) -> tuple[bool, str]:
    """Read updates.auto_update and updates.install_commit from config.yaml."""
    import yaml

    if not config_path.exists():
        return False, ""
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return False, ""

    updates = config.get("updates")
    if not isinstance(updates, dict):
        return False, ""

    auto_update = _as_bool(updates.get("auto_update"), False)
    install_commit = str(updates.get("install_commit", "") or "").strip()
    return auto_update, install_commit


def _current_repo_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _save_install_commit(config_path: Path, commit_hash: str) -> None:
    if not commit_hash or not config_path.exists():
        return

    import yaml

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return

    updates = config.get("updates")
    if not isinstance(updates, dict):
        updates = {}
        config["updates"] = updates
    updates["install_commit"] = commit_hash

    try:
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception:
        return


def _run_repo_update(
    repo_root: Path,
    refresh_dependencies: bool = True,
    config_path: Path | None = None,
) -> bool:
    """Pull latest code and optionally refresh dependencies."""
    if not (repo_root / ".git").exists():
        print(f"  Not a git checkout: {repo_root}")
        print("  Reinstall with the one-line installer, or update manually.")
        return False

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("  git is not installed or not available on PATH.")
        return False

    if result.returncode != 0:
        print("  Update failed.")
        err = (result.stderr or result.stdout).strip()
        if err:
            print(f"  {err}")
        print("  Resolve local git conflicts/changes, then retry.")
        return False

    out = result.stdout.strip()
    if out:
        print(out)
    else:
        print("  Updated repository.")

    commit_hash = _current_repo_commit(repo_root)
    if config_path is not None and commit_hash:
        _save_install_commit(config_path, commit_hash)
        print(f"  Recorded install commit: {commit_hash[:12]}")

    if not refresh_dependencies:
        return True

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
            cwd=repo_root,
        )
        print("  Dependencies refreshed.")
    except subprocess.CalledProcessError:
        print("  Repository updated, but dependency refresh failed.")
        print("  Run: pip install -r requirements.txt")
        return False
    return True


def _run_git(repo_root: Path, args: list[str], timeout_seconds: float = 6.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception:
        return None


def _count_commits_behind_upstream(repo_root: Path) -> int | None:
    """Return how many commits local HEAD is behind upstream; None if unknown."""
    if not (repo_root / ".git").exists():
        return None

    # Must have an upstream configured for the current branch.
    upstream = _run_git(
        repo_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        timeout_seconds=3.0,
    )
    if upstream is None or upstream.returncode != 0:
        return None

    # Refresh remote tracking refs best-effort; failures are treated as unknown.
    fetched = _run_git(repo_root, ["fetch", "--quiet"], timeout_seconds=8.0)
    if fetched is None or fetched.returncode != 0:
        return None

    counts = _run_git(repo_root, ["rev-list", "--left-right", "--count", "HEAD...@{u}"], timeout_seconds=3.0)
    if counts is None or counts.returncode != 0:
        return None

    raw = counts.stdout.strip()
    if not raw:
        return None

    # Output format: "<ahead>\t<behind>"
    parts = raw.replace("\t", " ").split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def cmd_setup(args: argparse.Namespace) -> None:
    """Run the interactive setup wizard."""
    from cli.setup import run_setup
    home = get_home_dir() if not args.home else Path(args.home).expanduser()
    run_setup(home_dir=home)


def cmd_start(args: argparse.Namespace) -> None:
    """Start the ClawQuant server."""
    config_path = get_home_dir() / "config.yaml"
    if not config_path.exists():
        print("  No configuration found. Run 'clawquant setup' first.")
        sys.exit(1)

    auto_update, install_commit = _load_update_preferences(config_path)
    repo_root = _repo_root()

    # Keep recorded install commit aligned with local HEAD.
    current_commit = _current_repo_commit(repo_root)
    if current_commit and current_commit != install_commit:
        _save_install_commit(config_path, current_commit)

    if auto_update:
        print("  Auto-update is enabled. Checking for updates...")
        _run_repo_update(repo_root, refresh_dependencies=True, config_path=config_path)
    else:
        behind = _count_commits_behind_upstream(repo_root)
        if behind and behind > 0:
            noun = "commit" if behind == 1 else "commits"
            print(f"  Auto-update is disabled. {behind} new {noun} available. Run 'clawquant update'.")

    from main import run, setup_logging
    setup_logging("INFO")

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


def cmd_update(args: argparse.Namespace) -> None:
    """Update local installation from GitHub using git pull."""
    config_path = get_home_dir() / "config.yaml"
    _run_repo_update(_repo_root(), refresh_dependencies=True, config_path=config_path)


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
        print("  Usage: clawquant plugin enable <name>")
        return

    import yaml
    home = get_home_dir()
    config_path = home / "config.yaml"

    if not config_path.exists():
        print("  No config file. Run 'clawquant setup' first.")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    def _enable_flag(entry: object) -> bool:
        if isinstance(entry, dict):
            entry["enabled"] = enable
            return True
        return False

    found = False

    # integrations.<name>
    integrations = config.get("integrations")
    if isinstance(integrations, dict) and name in integrations:
        found = _enable_flag(integrations.get(name)) or found

    # ai.providers.<name>
    ai = config.get("ai")
    if isinstance(ai, dict):
        providers = ai.get("providers")
        if isinstance(providers, dict) and name in providers:
            found = _enable_flag(providers.get(name)) or found

        agents = ai.get("agents")
        if isinstance(agents, dict) and name in agents:
            found = _enable_flag(agents.get(name)) or found

    # market_data.providers.<name>
    market_data = config.get("market_data")
    if isinstance(market_data, dict):
        providers = market_data.get("providers")
        if isinstance(providers, dict) and name in providers:
            found = _enable_flag(providers.get(name)) or found

    # risk.rules.<name>
    risk = config.get("risk")
    if isinstance(risk, dict):
        rules = risk.get("rules")
        if isinstance(rules, dict) and name in rules:
            found = _enable_flag(rules.get(name)) or found

    # scheduler.handlers.<name>
    scheduler = config.get("scheduler")
    if isinstance(scheduler, dict):
        handlers = scheduler.get("handlers")
        if isinstance(handlers, dict) and name in handlers:
            found = _enable_flag(handlers.get(name)) or found

    if found:
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        action = "Enabled" if enable else "Disabled"
        print(f"  {action}: {name}")
    else:
        if enable:
            from cli.setup import enable_plugin_with_setup
            if enable_plugin_with_setup(name, home):
                return
        print(f"  Plugin '{name}' not found in config. Run 'clawquant config' to set it up.")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="clawquant",
        description="ClawQuant -- Lightweight trading advisory system",
    )
    parser.add_argument("--home", type=str, default=None, help="ClawQuant home directory")

    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Run the interactive setup wizard")

    # start
    sub.add_parser("start", help="Start the ClawQuant server")

    # status
    sub.add_parser("status", help="Show system status")

    # update
    sub.add_parser("update", help="Pull latest code from GitHub and refresh dependencies")

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
        "update": cmd_update,
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

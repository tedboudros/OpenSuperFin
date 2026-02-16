# ClawQuant

An open-source, lightweight, event-driven trading advisory system powered by LLMs.

<p align="center">
  <img src="assets/long-logo.png" alt="ClawQuant long logo" width="560" />
</p>

ClawQuant currently runs as a conversational trading copilot over Telegram or Discord, with schedulable AI tasks and plugin tools for market news + web search.

Aye, this be the part where your crab does the analysis and you decide when to make a move.

```bash
# One-line install (checks Python 3.11+, clones repo, creates venv, installs deps, runs setup wizard)
curl -fsSL https://raw.githubusercontent.com/tedboudros/ClawQuant/main/install.sh | bash
```

## What Works Today

- **Conversational AI via Telegram + Discord**: one AI interface handles chat, trade confirmations, portfolio queries, and task management.
- **Multi-step tool loops**: the model can chain multiple tool calls in one turn before replying.
- **Live signal lifecycle for AI-proposed positions**: `open_potential_position` runs synchronous risk gating and then delivery (`signal.proposed -> signal.approved/rejected -> signal.delivered`).
- **Signal-ID confirmation tracking**: delivered signals require `confirm_signal(signal_id, entry_price, quantity)` or `skip_signal(signal_id, reason?)`, with one timeout reminder and pending state preserved.
- **Task scheduling with AI self-invocation**: `ai.run_prompt` lets scheduled jobs run through the same central AI stack.
- **Built-in schedulers/handlers**: `ai.run_prompt`, `news.briefing`, `notifications.send`, `comparison.weekly`.
- **Plugin-defined AI tools**: plugins can register tools dynamically (`get_tools` / `call_tool`).
- **News + web browsing tools**: `get_news` and `web_search` (Serper-backed, optional `as_of` cutoff).
- **Optional Selenium browser plugin**: exposes `open_browser`, `close_browser`, `list_saved_logins`, `run_selenium_code`, `get_browser_screenshot`, and `get_page_code`, with setup-managed saved login profiles.
- **Persistent conversation memory**: chat history is stored in SQLite (`conversation_messages`) and reused by scheduler runs.
- **One-time onboarding directive**: first user message persists an internal onboarding directive merged with the initial message.
- **Event-bus output dispatch**: all outbound text routes through `integration.output` and adapter-specific dispatch.
- **Plugin-scoped dependency installs**: heavy modules (like Selenium) are installed only when that plugin is enabled.

## Coming Soon (Documented Target-State, Not Fully Wired Yet)

- Full autonomous **orchestrator-driven** production pipeline from ambient live inputs (without explicit AI tool invocation).
- Additional integrations described in docs/examples (email/webhook/custom scrapers).
- Additional market-data providers described in docs/examples (e.g., CoinGecko).
- Auto-created default recurring learning tasks from config at startup.
- Simulator CLI/server integration and production validation coverage.

## How It Works (Current Runtime)

1. **Message arrives** via Telegram/Discord plugin.
2. **AI interface runs** with tool-calling (including plugin tools).
3. **Tools execute** actions (including proposing signals, recording confirmations, managing tasks, and fetching prices/news/web results).
4. **Signal proposals** (when used) pass through deterministic risk gating and adapter delivery.
5. **Responses/notifications are published** on `integration.output`.
6. **Output dispatcher delivers** via the right adapter/channel.
7. **Scheduler runs tasks** and can invoke the same AI (`ai.run_prompt`).

## Design

- **Fully abstracted core**. 8 protocols define extension points (market data, LLM providers, integrations, agents, risk rules, task handlers).
- **Self-describing plugins**. Most plugins declare `PLUGIN_META`; CLI auto-discovers and configures from metadata.
- **6 pip dependencies**. Everything else is Python stdlib.
- **File + SQLite state**. JSON/Markdown/JSONL files plus SQLite indexes and conversation history.
- **CLI-first setup**. One-line install, interactive setup wizard, all commands via `clawquant`.
- **Runs on a potato**. Single async Python process, ~100MB RAM.

## Vision

The long-term product vision (including dual AI/human portfolios, divergence learning, and one central AI across chat + scheduled runs) is documented in [docs/VISION.md](docs/VISION.md).

## Quick Start

```bash
# One-line install (checks Python 3.11+, clones repo, creates venv, installs deps, runs setup wizard)
curl -fsSL https://raw.githubusercontent.com/tedboudros/ClawQuant/main/install.sh | bash

# Interactive setup wizard
clawquant setup

# Start the server
clawquant start
```

Prefer manual install?

```bash
git clone https://github.com/tedboudros/ClawQuant.git
cd ClawQuant
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
clawquant setup
```

## Configuration

Run `clawquant setup` for first-time setup, or `clawquant config` to re-run it. The wizard writes:

- `~/.clawquant/config.yaml`
- `~/.clawquant/.env`

Setup asks whether to enable startup auto-updates (`git pull` before `clawquant start`), defaulting to your current setting on re-runs.

Useful commands:

```bash
clawquant status              # show system status
clawquant update              # pull latest code from GitHub + refresh deps
clawquant plugin list         # list available plugins
clawquant plugin <name>       # inspect/configure one plugin
clawquant plugin enable <n>   # enable plugin (runs setup flow if missing config)
clawquant plugin disable <n>  # disable plugin
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for details.

## Documentation

| Document | Description |
|----------|-------------|
| [Vision](docs/VISION.md) | Product north star and non-negotiable architecture principles |
| [Architecture](docs/ARCHITECTURE.md) | Current runtime architecture + target-state notes |
| [Data Models](docs/DATA_MODELS.md) | Core schemas and storage mappings |
| [Event Flows](docs/FLOWS.md) | Current live flows + target-state flows |
| [Learning Loop](docs/LEARNING_LOOP.md) | Divergence/memory system and current status |
| [Simulator](docs/SIMULATOR.md) | Simulator module status and limitations |
| [Configuration](docs/CONFIGURATION.md) | Current config reference and setup behavior |
| [Tech Decisions](docs/TECH_DECISIONS.md) | Architectural decisions + status caveats |

## License

MIT License

Copyright (c) 2026 ClawQuant

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

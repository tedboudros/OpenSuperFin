# OpenSuperFin

An open-source, lightweight, event-driven trading advisory system powered by LLMs.

OpenSuperFin monitors markets, news, and custom data sources, runs AI analysis pipelines, and delivers actionable trade signals to you via Telegram, email, or any integration you build. It works with any asset class -- stocks, crypto, ETFs, commodities, forex.

You trade. The system advises.

## How It Works

1. **Data flows in** -- market prices, emails from trusted contacts, news scrapers, custom sources
2. **AI analyzes** -- multi-agent pipeline produces investment memos and trade signals
3. **Risk gates** -- deterministic rules filter out signals that violate your risk parameters
4. **You get notified** -- signal + memo delivered via Telegram, email, etc.
5. **You decide** -- confirm, skip, or modify. The system tracks both outcomes.
6. **It learns** -- weekly comparison of AI vs. human decisions generates structured memories that improve future analysis

## Key Features

- **Multi-asset**: Stocks, crypto, ETFs, bonds, commodities, forex -- anything with a ticker
- **Dual portfolio**: Tracks what the AI would do AND what you actually do. Learns from the differences.
- **Investment memos**: Every signal comes with a structured memo (saved as Markdown) explaining the thesis, scenarios, risks, and monitoring plan
- **AI task factory**: The AI creates its own follow-up tasks -- daily monitoring, pre-earnings analysis, post-earnings review
- **Backtesting**: Run the same pipeline against historical data with zero lookahead bias. Compare LLM models side-by-side.
- **Plugin-based**: Integrations (Telegram, email, scrapers) are plugins. Add your own.

## Design

- **Fully abstracted core**. 8 protocols define every extension point (market data, LLM providers, integrations, agents, risk rules). The core never imports concrete implementations.
- **5 pip dependencies**. Everything else is Python stdlib.
- **File-based state**. Memos are Markdown. Positions are JSON. Logs are JSONL. All human-readable.
- **SQLite** for indexed queries (market data, memory search). No external database.
- **Runs on a potato**. Single async Python process. ~100MB RAM. No Docker, no Redis, no PostgreSQL.

## Architecture

```
Integration Plugins (Telegram, Email, Scrapers, ...)
        ↕ HTTP
Core Server (aiohttp, ~200 LOC)
        ↕ in-process
EventBus → Audit Log (JSONL)
        ↕
┌───────────┬──────────┬──────────┬───────────┐
│ Scheduler │ AI       │ Risk     │ Simulator │
│ (task     │ Engine   │ Engine   │ (backtest │
│  files)   │ (agents) │ (rules)  │  replay)  │
└───────────┴──────────┴──────────┴───────────┘
        ↕
Files + SQLite (~/.opensuperfin/)
```

See [docs/](docs/) for the full architecture documentation.

## Quick Start

```bash
# Clone
git clone https://github.com/tedboudros/OpenSuperFin.git
cd OpenSuperFin

# Install (5 dependencies)
pip install -r requirements.txt

# Configure
cp config.example.yaml ~/.opensuperfin/config.yaml
cp .env.example ~/.opensuperfin/.env
# Edit both files with your API keys and settings

# Run
python -m opensuperfin
```

## Configuration

Edit `~/.opensuperfin/config.yaml` for integrations, risk rules, AI providers, and scheduler settings. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full reference.

Secrets (API keys, tokens) go in `~/.opensuperfin/.env`.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | Core components and how they connect |
| [Data Models](docs/DATA_MODELS.md) | All data schemas with on-disk examples |
| [Event Flows](docs/FLOWS.md) | Concrete event flows and lifecycle examples |
| [Learning Loop](docs/LEARNING_LOOP.md) | Dual portfolio and AI-human learning system |
| [Simulator](docs/SIMULATOR.md) | Backtesting, model benchmarking, validation |
| [Configuration](docs/CONFIGURATION.md) | Full config.yaml reference |
| [Tech Decisions](docs/TECH_DECISIONS.md) | Architectural decision records |

## License

MIT

# Architectural Decisions

Key decisions, alternatives considered, and reasoning.

---

## ADR-1: File-Based State (OpenClaw Pattern)

**Decision**: All persistent state lives as files on disk. Memos are Markdown. Signals, positions, tasks, memories are JSON files. Audit log is JSONL. SQLite for indexed queries only.

**Alternatives considered**:
- PostgreSQL: Powerful queries, but requires a server process, 100MB+ RAM, installation
- SQLite for everything: Simpler code, but state isn't human-inspectable
- Pure files only: Maximum simplicity, but no indexing for time-series lookups

**Rationale**:
- You can `cat` a position, `grep` the audit log, open a memo in any editor
- State is trivially backed up (`cp -r ~/.opensuperfin/ backup/`)
- State is trivially inspectable and debuggable
- SQLite handles the one thing files are bad at: indexed time-series queries for market data and fast tag-based memory retrieval
- SQLite is Python stdlib -- zero install, zero RAM overhead, single file on disk
- Inspired by OpenClaw's "externalized memory as files" pattern

---

## ADR-2: Minimal Dependencies (5 Core Packages)

**Decision**: The core system has exactly 5 pip dependencies. Everything else is Python stdlib.

| Package | Purpose | Why not stdlib |
|---------|---------|----------------|
| `pydantic` | Data validation | stdlib has no declarative validation |
| `pyyaml` | Config parsing | stdlib only has `configparser` (INI format) |
| `python-dotenv` | .env loading | stdlib has no `.env` support |
| `aiohttp` | HTTP server | stdlib `http.server` is sync-only |
| `httpx` | HTTP client | stdlib `urllib` is sync and painful |

**Alternatives considered**:
- FastAPI + uvicorn: More features, but pulls in starlette, pydantic (already have it), jinja2, etc. 20+ transitive deps
- Flask: Not async-native
- No HTTP server (Telegram only): Couples the core to a specific integration

**Rationale**:
- Fewer dependencies = fewer supply chain risks, fewer version conflicts, smaller install
- The system should run on a Raspberry Pi, a cheap VPS, or a laptop
- Integration plugins add their own dependencies (e.g., `python-telegram-bot`) -- only installed if used
- stdlib `sqlite3`, `json`, `asyncio`, `pathlib`, `imaplib`, `smtplib`, `math`, `statistics` cover most needs

---

## ADR-3: Lightweight Core HTTP Server

**Decision**: The core is an `aiohttp` HTTP server with ~10 routes. Integrations (Telegram, email, etc.) are plugins that connect to it.

**Alternatives considered**:
- Telegram as the core interface: Couples system to one integration
- FastAPI: Heavier, more features than we need
- No server (library-only): Harder for plugins to connect

**Rationale**:
- The core should be integration-agnostic. Telegram is a plugin, not the system.
- `aiohttp` gives us async HTTP server + WebSocket in one lightweight package
- ~200 lines of routing code, no framework magic
- Any integration can push/pull data via simple HTTP
- The AI interface is an HTTP endpoint that any frontend can call

---

## ADR-4: Simple Asyncio Scheduler (No APScheduler)

**Decision**: The scheduler is a simple `asyncio` loop that reads task JSON files from a directory every 60 seconds.

**Alternatives considered**:
- APScheduler: Full-featured, DB-backed, but adds dependency + complexity
- Celery: Way too heavy (requires Redis/RabbitMQ)
- System cron: Can't be controlled by the AI at runtime

**Rationale**:
- A task is a JSON file. Creating a task = writing a file. Deleting = removing a file.
- The scheduler loop is ~50 lines: read files, check cron/datetime, fire events
- The AI creates tasks by writing JSON files -- no API needed, no ORM, no migrations
- Cron expression parsing is ~30 lines of Python or a tiny single-file library
- State survives restarts (files persist on disk)
- Human-inspectable: `ls tasks/` shows all scheduled tasks

---

## ADR-5: Event Bus as In-Process Pub/Sub

**Decision**: The event bus is an in-process Python class using `asyncio`. ~100 lines of code. No external message queue.

**Rationale**:
- For a single-process system, in-process pub/sub is the simplest possible implementation
- Events are persisted to daily JSONL files for audit
- Correlation IDs link related events into decision chains
- The bus is defined as a Protocol -- if we ever need Redis Streams or NATS, swap the implementation without changing any subscriber code

---

## ADR-6: Risk Engine Separate from AI (Zero LLM)

**Decision**: The Risk Engine is deterministic, has zero LLM involvement, and cannot be overridden by the AI.

**Rationale**:
- An LLM can hallucinate confidence levels or rationalize away risk
- Hard position limits, drawdown rules, and concentration limits MUST be enforced deterministically
- The AI has an advisory "Risk Analyst" agent for qualitative assessment in the memo
- The Risk Engine is the quantitative gate: pure math, no AI
- If the AI could override risk rules, a single hallucination could cause serious damage

---

## ADR-7: Dual Portfolio Tracking

**Decision**: Maintain two parallel portfolios (AI paper + human actual).

**Rationale**:
- The AI portfolio shows pure decision quality (always executes signals)
- The human portfolio shows actual results (confirms, skips, independent trades)
- Divergences feed the learning loop
- Risk engine validates against AI portfolio (the consistent one)
- Both are just JSON files in `positions/ai/` and `positions/human/`

---

## ADR-8: Learning Loop via Memories

**Decision**: Generate structured Memory entries from portfolio divergences, store as JSON files + SQLite index, include relevant memories in future AI context packs.

**Rationale**:
- Memories are interpretable, inspectable, deletable JSON files
- No model fine-tuning required -- just structured context
- Tagged and filtered for relevance (ticker, sector, catalyst, recency)
- The simulator can pre-generate memories from historical data
- The AI literally gets smarter from disagreements with the human

---

## ADR-9: TimeContext for Simulation Integrity

**Decision**: All data queries pass through a TimeContext that filters by `available_at` timestamp.

**Rationale**:
- Single data store for production and simulation
- Filtering is automatic at the data layer
- Components don't know if they're in production or simulation
- `available_at` vs `timestamp` handles delayed-release data correctly (CPI, earnings)
- Zero lookahead bias guaranteed by design

---

## ADR-10: Fully Abstracted Core via Protocols

**Decision**: The core defines 8 Python `Protocol` classes. Every external interaction goes through a protocol. The core never imports concrete implementations.

**The 8 Protocols**:

| Protocol | Abstracts | Default Implementation |
|----------|-----------|----------------------|
| `EventBus` | Inter-component messaging | `AsyncIOBus` (in-process pub/sub) |
| `MarketDataProvider` | Price/market data fetching | Yahoo Finance, CoinGecko, etc. |
| `InputAdapter` | Receiving external data | Telegram, Email, Webhooks, Scrapers |
| `OutputAdapter` | Delivering signals outward | Telegram, Email, Webhooks |
| `LLMProvider` | Language model API calls | OpenAI, Anthropic, Google, local |
| `AIAgent` | Analysis logic | Macro, Rates, Company agents |
| `RiskRule` | Signal validation rules | Confidence, Concentration, Drawdown |
| `TaskHandler` | Scheduled task execution | Monitoring, DataSync, Comparison |

**Key principle**: The core imports protocols. Plugins import the core. Never the reverse.

**Rationale**:
- Structural subtyping: if your class has the right methods, it implements the protocol. No inheritance required.
- Full static type checking (mypy, pyright)
- Easy to test with mocks (any object with the right shape works)
- Plugins are regular Python classes, no framework decorators
- Adding a new market data source, LLM provider, or integration is just implementing a protocol and adding config
- The core works with any combination of plugins -- swap Yahoo Finance for CoinGecko without touching a line of core code

---

## ADR-11: No Automated Execution

**Decision**: The system produces signals. The human trades.

**Rationale**:
- Safety (no runaway losses from bugs or hallucinations)
- Regulatory simplicity
- Trust building (human validates every recommendation)
- The dual portfolio still tracks hypothetical auto-execution
- Can be added later as an opt-in integration once the system has a proven track record

---

## ADR-12: LLM API Calls via httpx (No SDKs Required)

**Decision**: Call LLM APIs directly via `httpx` HTTP requests. OpenAI/Anthropic SDKs are optional.

**Rationale**:
- The OpenAI and Anthropic APIs are simple JSON-over-HTTP
- Direct calls via `httpx` avoid SDK dependencies and version churn
- The `LLMProvider` protocol abstracts the API shape
- SDKs can be used if already installed, but aren't required
- Fewer dependencies = less breakage from SDK updates

---

## Tech Stack Summary

| Component | Technology | Lines of Code (est.) |
|-----------|-----------|---------------------|
| HTTP Server | `aiohttp` | ~200 |
| Event Bus | `asyncio` pub/sub | ~100 |
| Scheduler | `asyncio` loop + file reader | ~80 |
| Data Layer | `sqlite3` + `json` + `pathlib` | ~150 |
| Risk Engine | Pure Python math | ~100 |
| AI Engine | `httpx` → LLM APIs | ~300 |
| Config | `pydantic` + `pyyaml` | ~100 |

**Total core**: ~1,000 lines of Python. The rest is agent prompts and integration plugins.

---

## Directory Structure

```
opensuperfin/
    core/                          # DEFINES WHAT (protocols + models)
        protocols.py               # All 8 protocols in one file
        bus.py                     # EventBus default impl (~100 lines)
        config.py                  # Config loader (YAML + .env)
        time_context.py            # TimeContext (production vs simulation)
        registry.py                # Plugin registry + discovery
        models/
            events.py              # Event schema
            signals.py             # Signal, Position
            memos.py               # InvestmentMemo
            market.py              # MarketData
            tasks.py               # Task
            memories.py            # Memory
            simulations.py         # SimulationRun
        data/
            store.py               # File + SQLite storage layer
            queries.py             # TimeContext-aware data access

    plugins/                       # IMPLEMENTS HOW (concrete implementations)
        market_data/
            yahoo_finance.py       # MarketDataProvider for stocks/ETFs/forex
            coingecko.py           # MarketDataProvider for crypto
        integrations/
            telegram.py            # InputAdapter + OutputAdapter
            email.py               # InputAdapter + OutputAdapter (stdlib)
            webhook.py             # InputAdapter + OutputAdapter
            custom_loader.py       # Loads user scraper scripts
        ai_providers/
            openai.py              # LLMProvider via httpx
            anthropic.py           # LLMProvider via httpx
            google.py              # LLMProvider via httpx
        agents/
            macro.py               # AIAgent: Macro Strategist
            rates.py               # AIAgent: Rates Strategist
            company.py             # AIAgent: Company Analyst
        risk_rules/
            confidence.py          # RiskRule
            concentration.py       # RiskRule
            frequency.py           # RiskRule
            drawdown.py            # RiskRule
        task_handlers/
            monitoring.py          # TaskHandler: position monitoring
            data_sync.py           # TaskHandler: market data sync
            comparison.py          # TaskHandler: AI-vs-human comparison
            analysis.py            # TaskHandler: periodic analysis

    engine/                        # ORCHESTRATION (wires protocols together)
        orchestrator.py            # Multi-agent pipeline
        interface.py               # Chat interface (tool-use)
        tools.py                   # AI tool definitions
        memory.py                  # Memory retrieval + relevance scoring
        prompts/                   # Prompt templates

    scheduler/
        runner.py                  # Asyncio scheduler loop
        cron.py                    # Cron expression parser

    risk/
        engine.py                  # Risk engine (uses RiskRule protocol)
        portfolio.py               # Dual portfolio tracker

    simulator/
        engine.py                  # Simulation orchestrator
        replayer.py                # Event replay
        mocks.py                   # Mock OutputAdapter for capturing signals
        metrics.py                 # Performance math (pure Python)

    server.py                      # aiohttp server + routes (~200 lines)
    main.py                        # Entrypoint
```

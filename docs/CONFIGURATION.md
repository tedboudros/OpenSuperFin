# Configuration

OpenSuperFin uses two configuration files:

- `config.yaml` -- All structured settings
- `.env` -- Secrets only (API keys, passwords, tokens)

Secrets are referenced in `config.yaml` via `${ENV_VAR}` syntax and resolved at startup from `.env` or environment variables.

All state is stored under a configurable home directory (default: `~/.opensuperfin/`).

---

## Full Configuration Reference

```yaml
# config.yaml

# ─────────────────────────────────────────────────────────────────────
# GENERAL
# ─────────────────────────────────────────────────────────────────────

home_dir: ~/.opensuperfin         # where all state files live
server:
  host: 127.0.0.1                 # bind address
  port: 8321                      # port for the core HTTP server


# ─────────────────────────────────────────────────────────────────────
# INTEGRATIONS (plugins)
# ─────────────────────────────────────────────────────────────────────

integrations:

  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    channels:
      - id: personal
        chat_id: "123456789"
        direction: both             # input + output
        input_types:
          - position_confirmation   # "bought NVDA at 130"
          - position_rejection      # "skip"
          - user_initiated_trade    # "bought TSLA on my own at 245"
          - question                # "what's my portfolio?"
        output_types:
          - signal
          - alert
          - report
          - simulation_result

      - id: signals_group
        chat_id: "987654321"
        direction: output
        output_types:
          - signal

  email:
    enabled: true
    imap:
      host: imap.gmail.com
      port: 993
      username: ${EMAIL_USER}
      password: ${EMAIL_APP_PASSWORD}
      check_interval: 60s
    smtp:
      host: smtp.gmail.com
      port: 587
      username: ${EMAIL_USER}
      password: ${EMAIL_APP_PASSWORD}
    watch_rules:
      - from: "investor.friend@email.com"
        label: trusted_investor
        priority: high
      - from: "newsletter@macro-research.com"
        label: research
        priority: medium
      - subject_contains: "URGENT"
        label: urgent
        priority: high

  webhook:
    enabled: false
    listen_port: 8080
    endpoints:
      - path: /webhooks/news
        label: news_feed
        priority: medium
        auth_token: ${WEBHOOK_AUTH_TOKEN}

  custom:
    defense_scraper:
      enabled: true
      direction: input
      script: ./scrapers/defense_contracts.py
      schedule: "0 9 * * 1-5"


# ─────────────────────────────────────────────────────────────────────
# MARKET DATA PROVIDERS (MarketDataProvider protocol)
# ─────────────────────────────────────────────────────────────────────
# Each provider implements the MarketDataProvider protocol.
# Multiple providers can be active simultaneously.
# Tickers are routed to the provider that supports them.

market_data:
  poll_interval: 5m               # default polling interval
  history_depth: 2y               # how much history to load on startup

  providers:
    yahoo_finance:
      enabled: true
      tickers:                    # stocks, ETFs, indices, forex
        - AAPL
        - NVDA
        - SPY
        - QQQ
        - TLT
        - GLD
        - EURUSD=X

    coingecko:
      enabled: true
      tickers:                    # crypto
        - BTC
        - ETH
        - SOL

    # Add any provider that implements MarketDataProvider:
    # binance:
    #   enabled: true
    #   api_key: ${BINANCE_API_KEY}
    #   tickers: [BTCUSDT, ETHUSDT]
    #
    # alpha_vantage:
    #   enabled: false
    #   api_key: ${ALPHA_VANTAGE_KEY}
    #   tickers: [GDP, CPI, FEDFUNDS]


# ─────────────────────────────────────────────────────────────────────
# RISK ENGINE
# ─────────────────────────────────────────────────────────────────────

risk:
  rules:
    confidence:
      min_confidence: 0.60

    concentration:
      max_single_position: 0.15
      max_sector_exposure: 0.30

    frequency:
      max_signals_per_day: 5

    drawdown:
      max_portfolio_drawdown: 0.15


# ─────────────────────────────────────────────────────────────────────
# POSITION TRACKING
# ─────────────────────────────────────────────────────────────────────

position_tracking:
  confirmation_timeout: 4h
  allow_user_initiated: true


# ─────────────────────────────────────────────────────────────────────
# AI ENGINE
# ─────────────────────────────────────────────────────────────────────

ai:
  default_provider: anthropic

  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}
      model: your-preferred-claude-model  # e.g., claude-sonnet-4-20250514
      max_tokens: 4096
      temperature: 0.3

    openai:
      api_key: ${OPENAI_API_KEY}
      model: your-preferred-gpt-model     # e.g., gpt-4o
      max_tokens: 4096
      temperature: 0.3

    openai_mini:
      api_key: ${OPENAI_API_KEY}
      model: your-preferred-cheap-model   # e.g., gpt-4o-mini
      max_tokens: 2048
      temperature: 0.2

  # Route different task types to different providers
  task_routing:
    research: anthropic           # deep analysis -> best model
    monitoring: openai_mini       # daily checks -> cheap model
    comparison: openai_mini       # memory generation -> cheap model
    interface: anthropic          # chat -> best model

  agents:
    macro:
      enabled: true
      description: "Macro Strategist"
    rates:
      enabled: true
      description: "Rates Strategist"
    company:
      enabled: true
      description: "Company Analyst"
    risk_advisory:
      enabled: true
      description: "Risk Analyst (advisory only)"


# ─────────────────────────────────────────────────────────────────────
# LEARNING LOOP
# ─────────────────────────────────────────────────────────────────────

learning:
  comparison_schedule: "0 9 * * 0"  # Sunday 9am
  min_outcome_period: 7d
  max_memories_in_context: 10
  memory_relevance_window: 90d


# ─────────────────────────────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────────────────────────────

scheduler:
  timezone: America/New_York
  check_interval: 60s             # how often to scan task files

  default_tasks:
    - name: "Daily market data sync"
      type: recurring
      schedule: "0 16 * * 1-5"
      handler: data_sync.market_close

    - name: "Weekly portfolio comparison"
      type: comparison
      schedule: "0 9 * * 0"
      handler: comparison.weekly

    - name: "Daily portfolio summary"
      type: recurring
      schedule: "0 17 * * 1-5"
      handler: analysis.daily_summary


# ─────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────

logging:
  level: INFO
  audit_events: true              # persist all events to JSONL files
  llm_calls: true                 # log LLM API calls for cost tracking
```

---

## Environment Variables (.env)

```bash
# .env -- secrets only, NEVER committed to git

# AI Providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

# Email
EMAIL_USER=trading@yourdomain.com
EMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Webhooks (optional)
WEBHOOK_AUTH_TOKEN=your-secret-token
```

---

## Configuration Loading

At startup:

1. Load `.env` into environment (via `python-dotenv`)
2. Load `config.yaml`, resolve `${ENV_VAR}` references
3. Validate against Pydantic settings models
4. Fail fast with clear errors if required config is missing
5. Create `home_dir` and subdirectories if they don't exist
6. Initialize `db.sqlite` if it doesn't exist
7. Write default task files if `tasks/` is empty

---

## Custom Scraper Interface

Users write Python scripts in a `scrapers/` directory:

```python
# scrapers/defense_contracts.py

async def scrape() -> list[dict]:
    """
    Returns a list of events to publish.
    Each dict becomes the payload of an integration.input event.
    """
    return [
        {
            "headline": "DoD awards $2.1B contract to Palantir",
            "ticker": "PLTR",
            "source_url": "https://...",
        }
    ]

# Optional metadata
META = {
    "name": "Defense Contracts Scraper",
    "description": "Monitors defense contract awards",
}
```

The custom loader imports and runs these on the configured schedule.

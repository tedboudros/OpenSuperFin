# Configuration

ClawQuant uses:
- `config.yaml` for structured config
- `.env` for secrets

Default home: `~/.clawquant/` (override with `CLAWQUANT_HOME`).

---

## Setup Workflow

```bash
clawquant setup
```

What setup does today:
- Discovers plugins from `PLUGIN_META`
- Prompts for config fields
- Asks whether startup auto-update should be enabled (defaults to current value on re-runs)
- Lets you skip sections if required fields already exist
- Runs plugin-specific setup flows where needed (e.g., selenium saved login profiles)
- Writes `config.yaml` and `.env`
- Installs plugin-specific pip deps when required
  - Optional heavy deps are only installed if that plugin is enabled

Re-run full setup:

```bash
clawquant config
```

Enable a plugin later:

```bash
clawquant plugin enable <plugin_name>
```

If plugin config is missing, `plugin enable` runs that plugin's setup flow and persists values.

Manual update:

```bash
clawquant update
```

On successful manual or startup auto-update, `updates.install_commit` is rewritten to the current `HEAD`.

---

## Current Runtime-Supported Plugins

### Integrations
- `telegram`
- `discord`

### Market Data
- `yahoo_finance`

### AI Providers
- `openai`
- `anthropic`
- `openrouter`

### Task Handlers (loaded by runtime)
- `ai.run_prompt`
- `news.briefing`
- `notifications.send`
- `comparison.weekly`
- `web.search` (tool-oriented handler; scheduled run is `no_action`)
- `browser.selenium` (optional, only when `selenium_browser` is enabled)

---

## Current `config.yaml` Example

```yaml
home_dir: ~/.clawquant

server:
  host: 127.0.0.1
  port: 8321

updates:
  auto_update: true
  install_commit: "3f2abcde1234567890fedcba0987654321abcd12"

integrations:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    channels:
      - id: personal
        chat_id: "123456789"
        direction: both  # both | input | output

  discord:
    enabled: false
    bot_token: ${DISCORD_BOT_TOKEN}
    poll_interval_seconds: 3
    channels:
      - id: personal
        chat_id: "123456789012345678"  # Discord channel ID
        direction: both

market_data:
  poll_interval: 5m
  history_depth: 2y
  providers:
    yahoo_finance:
      enabled: true
      tickers:
        - SPY
        - QQQ
        - NVDA
        - BTC-USD

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

position_tracking:
  confirmation_timeout: 4h
  allow_user_initiated: true

ai:
  default_provider: anthropic
  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}
      model: claude-sonnet-4-20250514
      max_tokens: 4096
      temperature: 0.3
    openai:
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o
      max_tokens: 4096
      temperature: 0.3
    openrouter:
      api_key: ${OPENROUTER_API_KEY}
      model: openai/gpt-4o
      max_tokens: 4096
      temperature: 0.3

  # parsed by config model; currently lightly used in runtime wiring
  task_routing: {}

  # parsed by config model; runtime currently wires 'macro' agent
  agents:
    macro:
      enabled: true

learning:
  comparison_schedule: "0 9 * * 0"
  min_outcome_period: 7d
  max_memories_in_context: 10
  memory_relevance_window: 90d

scheduler:
  timezone: America/New_York
  check_interval: 60s
  default_tasks: []  # currently not auto-created by startup
  handlers:
    selenium_browser:
      enabled: false
      default_browser: chrome
      headless: true
      page_code_max_chars: 6000
      logins_b64: ${SELENIUM_LOGINS_B64}

logging:
  level: INFO
  audit_events: true
  llm_calls: true
```

---

## Current `.env` Example

```bash
# integrations
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...

# ai providers
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENROUTER_API_KEY=...

# plugin tools
SERPER_API_KEY=...
SELENIUM_LOGINS_B64=...
```

### Selenium Login Profiles

When `selenium_browser` is enabled, setup includes an interactive credential-profile step.

- Profiles are stored as secret env data (`SELENIUM_LOGINS_B64`).
- AI can discover profile IDs via `list_saved_logins`.
- During `run_selenium_code`, use helper `get_saved_login("<profile_id>")` to retrieve username/password.
- Credentials are not intended to be echoed in normal assistant responses.

---

## Notes on Config Fields That Are Not Fully Wired Yet

- `scheduler.default_tasks`: parsed, but startup currently does not auto-create those tasks.
- `learning.comparison_schedule`: parsed, but no automatic task creation from this value.
- `ai.task_routing`: parsed, but runtime mostly uses default provider flow today.
- `ai.agents`: runtime currently wires `macro`; additional agent names in config are target-state.

---

## Coming Soon (Doc References You May Still See Elsewhere)

The following appear in older architecture examples but are not wired in the current runtime:
- Email/webhook/custom scraper integrations
- CoinGecko and additional market providers as first-class runtime options
- End-to-end autonomous signal pipeline wiring from live inputs

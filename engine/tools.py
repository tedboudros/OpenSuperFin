"""Tool definitions for the AI interface.

These are the tools the AI can call to interact with the system.
Defined in OpenAI function-calling format (works with any provider).
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_potential_position",
            "description": "Propose a new trade signal for synchronous risk evaluation and delivery lifecycle handling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., NVDA, BTC-USD, AAPL)",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Proposed signal direction",
                    },
                    "catalyst": {
                        "type": "string",
                        "description": "Why this position is being proposed",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Signal confidence from 0.0 to 1.0",
                    },
                    "entry_target": {
                        "type": "number",
                        "description": "Target entry price for the signal",
                    },
                    "stop_loss": {
                        "type": "number",
                        "description": "Optional stop-loss price",
                    },
                    "take_profit": {
                        "type": "number",
                        "description": "Optional take-profit price",
                    },
                    "horizon": {
                        "type": "string",
                        "description": "Expected holding period (e.g., 1-3 months)",
                    },
                },
                "required": [
                    "ticker",
                    "direction",
                    "catalyst",
                    "confidence",
                    "entry_target",
                    "horizon",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_signal",
            "description": "Confirm a delivered signal using explicit signal_id, entry price, and quantity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": "Signal identifier (e.g., sig_ab12cd34ef56)",
                    },
                    "entry_price": {
                        "type": "number",
                        "description": "Actual executed entry price",
                    },
                    "quantity": {
                        "type": "number",
                        "description": "Executed position quantity (required)",
                    },
                },
                "required": ["signal_id", "entry_price", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_signal",
            "description": "Skip a delivered signal using its signal_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": "Signal identifier (e.g., sig_ab12cd34ef56)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for skipping",
                    },
                },
                "required": ["signal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_position",
            "description": "User reports they closed/exited a position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol",
                    },
                    "close_price": {
                        "type": "number",
                        "description": "The price at which the position was closed",
                    },
                },
                "required": ["ticker", "close_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "user_initiated_trade",
            "description": "User reports a trade they took on their own initiative, not from an AI signal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["long", "short"],
                        "description": "Trade direction",
                    },
                    "entry_price": {
                        "type": "number",
                        "description": "Entry price",
                    },
                    "size": {
                        "type": "number",
                        "description": "Number of units (optional)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why the user took this trade",
                    },
                },
                "required": ["ticker", "direction", "entry_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Get current portfolio state. Can show AI portfolio, human portfolio, or both.",
            "parameters": {
                "type": "object",
                "properties": {
                    "portfolio_type": {
                        "type": "string",
                        "enum": ["ai", "human", "both"],
                        "description": "Which portfolio to show (default: both)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price",
            "description": "Get the latest price for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., NVDA, BTC-USD, SPY)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all scheduled tasks (monitoring, analysis, etc.).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_task_handlers",
            "description": "List all registered task handler names that can be used in create_task.handler.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new scheduled task. For recurring monitoring tasks, prefer handler ai.run_prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Human-readable task name",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["one_off", "recurring", "research"],
                        "description": "Task type",
                    },
                    "handler": {
                        "type": "string",
                        "description": "Registered task handler name from list_task_handlers (for monitoring prefer ai.run_prompt)",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "Cron schedule for recurring tasks (e.g., '0 16 * * 1-5')",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "ISO datetime for one-off tasks",
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameters to pass to the handler. For ai.run_prompt include params.prompt with the per-run execution instruction. A run can return exactly [NO_REPLY] to skip user notification.",
                    },
                },
                "required": ["name", "handler"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Delete a scheduled task by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to delete",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task_by_name",
            "description": "Delete scheduled task(s) by name match when the user doesn't provide an ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Task name or a unique part of it",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memories",
            "description": "View learning memories from past AI-vs-human divergences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Filter by ticker (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max memories to return (default: 10)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": "Trigger an on-demand analysis for a specific ticker or topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "What to analyze (e.g., a ticker, a macro event, a sector)",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_signals",
            "description": "List recent signals (trade recommendations).",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["proposed", "approved", "rejected", "delivered"],
                        "description": "Filter by signal status (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max signals to return (default: 10)",
                    },
                },
            },
        },
    },
]

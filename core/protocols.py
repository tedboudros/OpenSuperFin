"""Core protocols -- the 8 extension points that define the system.

The core imports these protocols. Plugins implement them.
The core NEVER imports concrete implementations.

All protocols use Python's structural subtyping (typing.Protocol):
if your class has the right methods, it implements the protocol.
No inheritance required.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Coroutine, Protocol, runtime_checkable

from core.models.context import ContextPack, PortfolioSummary
from core.models.events import Event
from core.models.market import MarketData
from core.models.memos import InvestmentMemo
from core.models.signals import RiskResult, RuleEvaluation, Signal
from core.models.tasks import TaskResult


# ---------------------------------------------------------------------------
# 1. EventBus -- inter-component communication
# ---------------------------------------------------------------------------

@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe event bus. All inter-component communication
    flows through this protocol.

    Default implementation: AsyncIOBus (in-process pub/sub with JSONL audit).
    Can be swapped for Redis Streams, NATS, etc.
    """

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers of its type."""
        ...

    def subscribe(self, event_type: str, callback: Callable[[Event], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for events of the given type."""
        ...

    def unsubscribe(self, event_type: str, callback: Callable[[Event], Coroutine[Any, Any, None]]) -> None:
        """Remove a previously registered callback."""
        ...


# ---------------------------------------------------------------------------
# 2. MarketDataProvider -- fetch price/market data for any asset
# ---------------------------------------------------------------------------

@runtime_checkable
class MarketDataProvider(Protocol):
    """Fetches market data for tickers it supports.

    Multiple providers can be active simultaneously (e.g., Yahoo Finance
    for stocks, CoinGecko for crypto). The system routes tickers to the
    provider that supports them.
    """

    @property
    def name(self) -> str:
        """Unique provider name, e.g. 'yahoo_finance', 'coingecko'."""
        ...

    async def fetch(
        self,
        tickers: list[str],
        start: datetime,
        end: datetime,
    ) -> list[MarketData]:
        """Fetch historical market data for the given tickers and date range.

        Each returned MarketData must have `available_at` set correctly
        for simulation integrity.
        """
        ...

    def supports(self, ticker: str) -> bool:
        """Return True if this provider can handle the given ticker."""
        ...


# ---------------------------------------------------------------------------
# 3. InputAdapter -- receive data from external sources
# ---------------------------------------------------------------------------

@runtime_checkable
class InputAdapter(Protocol):
    """Receives data from an external source and pushes events into the system.

    Examples: Telegram bot, email IMAP poller, webhook receiver, custom scrapers.
    """

    @property
    def name(self) -> str:
        """Unique adapter name, e.g. 'telegram', 'email'."""
        ...

    async def start(self) -> None:
        """Start listening for input (connect, poll, etc.)."""
        ...

    async def stop(self) -> None:
        """Stop listening and clean up."""
        ...

    def on_message(self, callback: Callable[[dict], Coroutine[Any, Any, None]]) -> None:
        """Register a callback invoked when new data arrives.

        The callback receives a dict payload which will be wrapped in an
        integration.input Event by the core.
        """
        ...


# ---------------------------------------------------------------------------
# 4. OutputAdapter -- deliver signals to external destinations
# ---------------------------------------------------------------------------

@runtime_checkable
class OutputAdapter(Protocol):
    """Delivers signals and notifications to an external destination.

    Examples: Telegram message, email, webhook POST.
    """

    @property
    def name(self) -> str:
        """Unique adapter name, e.g. 'telegram', 'email'."""
        ...

    async def send(self, signal: Signal, memo: InvestmentMemo | None = None) -> DeliveryResult:
        """Deliver a signal (and optionally its memo) to the destination.

        Returns a DeliveryResult indicating success/failure.
        """
        ...


class DeliveryResult:
    """Result of an OutputAdapter.send() call."""

    def __init__(self, success: bool, adapter: str, message: str = ""):
        self.success = success
        self.adapter = adapter
        self.message = message

    def __repr__(self) -> str:
        status = "ok" if self.success else "failed"
        return f"DeliveryResult({status}, {self.adapter})"


# ---------------------------------------------------------------------------
# 5. LLMProvider -- call language model APIs
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMProvider(Protocol):
    """Abstracts language model API calls.

    Implementations call LLM APIs via httpx (no SDK required).
    Supports OpenAI, Anthropic, Google, local models, etc.
    """

    @property
    def name(self) -> str:
        """Provider name, e.g. 'openai', 'anthropic', 'google'."""
        ...

    async def complete(self, messages: list[dict], **kwargs: Any) -> str:
        """Send messages to the model and return the text response."""
        ...

    async def tool_call(
        self,
        messages: list[dict],
        tools: list[dict],
        **kwargs: Any,
    ) -> ToolCallResult:
        """Send messages with tool definitions and return tool call results."""
        ...


class ToolCallResult:
    """Result of an LLMProvider.tool_call() invocation."""

    def __init__(
        self,
        text: str = "",
        tool_calls: list[dict] | None = None,
        usage: dict | None = None,
    ):
        self.text = text
        self.tool_calls = tool_calls or []
        self.usage = usage or {}

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ---------------------------------------------------------------------------
# 6. AIAgent -- individual analysis agent
# ---------------------------------------------------------------------------

@runtime_checkable
class AIAgent(Protocol):
    """A self-contained analysis unit in the agent pipeline.

    Each agent analyzes a ContextPack and produces an AgentOutput.
    Agents are pluggable -- add new ones by implementing this protocol.
    """

    @property
    def name(self) -> str:
        """Agent name, e.g. 'macro', 'rates', 'company'."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of what this agent does."""
        ...

    async def analyze(self, context: ContextPack) -> AgentOutput:
        """Analyze the context pack and produce structured output."""
        ...


class AgentOutput:
    """Output produced by an AIAgent."""

    def __init__(
        self,
        agent_name: str,
        analysis: str,
        confidence: float = 0.0,
        suggested_direction: str | None = None,
        key_factors: list[str] | None = None,
    ):
        self.agent_name = agent_name
        self.analysis = analysis
        self.confidence = confidence
        self.suggested_direction = suggested_direction
        self.key_factors = key_factors or []


# ---------------------------------------------------------------------------
# 7. RiskRule -- evaluate a signal against a rule
# ---------------------------------------------------------------------------

@runtime_checkable
class RiskRule(Protocol):
    """A single risk rule that evaluates signals.

    The Risk Engine collects all registered RiskRules and runs them
    against every proposed signal. ALL rules must pass for approval.
    """

    @property
    def name(self) -> str:
        """Rule name, e.g. 'confidence', 'concentration', 'drawdown'."""
        ...

    def evaluate(self, signal: Signal, portfolio: PortfolioSummary) -> RuleEvaluation:
        """Evaluate whether the signal passes this rule.

        This is intentionally synchronous -- risk rules must be
        deterministic and fast. No LLM calls, no I/O.
        """
        ...


# ---------------------------------------------------------------------------
# 8. TaskHandler -- execute a scheduled task
# ---------------------------------------------------------------------------

@runtime_checkable
class TaskHandler(Protocol):
    """Handles execution of a scheduled task.

    The scheduler looks up handlers by name from the registry when
    a task's schedule matches.
    """

    @property
    def name(self) -> str:
        """Handler name, matches the 'handler' field in task JSON files.
        e.g. 'monitoring.check_exit', 'data_sync.market_close'.
        """
        ...

    async def run(self, params: dict) -> TaskResult:
        """Execute the task with the given parameters."""
        ...

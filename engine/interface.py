"""AI Interface -- conversational controller with tool-use.

This is the brain behind all user interactions. It receives raw messages
(from Telegram, HTTP API, or any integration), understands intent via LLM
tool-calling, executes the appropriate actions, and returns a response.

The AI interface handles ANY language -- the LLM does the understanding,
not regex or keyword matching.
"""

from __future__ import annotations

import json
import logging
from inspect import isawaitable
from datetime import datetime, timedelta, timezone
from typing import Any

from core.bus import AsyncIOBus
from core.data.store import Store
from core.models.events import Event, EventTypes
from core.models.memories import Memory
from core.models.signals import Position, Signal
from core.models.tasks import Task
from core.protocols import LLMProvider
from core.registry import PluginRegistry
from engine.tools import TOOLS
from risk.portfolio import PortfolioTracker
from scheduler.runner import Scheduler

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the AI assistant for ClawQuant, a trading advisory system.

You help the user manage their trading activity. You can:
- Record trades they've made (confirm_trade, close_position, user_initiated_trade)
- Record trades they've skipped (skip_trade)
- Show portfolio state (get_portfolio)
- Look up prices (get_price)
- Manage scheduled tasks (list_tasks, create_task, delete_task)
- List schedulable handlers (list_task_handlers)
- View learning memories (get_memories)
- Trigger analysis (run_analysis)
- Show recent signals (get_signals)

IMPORTANT RULES:
- When the user tells you about a trade they made, use the appropriate tool to record it.
- When they ask about their portfolio or positions, use get_portfolio.
- When they want to skip a signal, use skip_trade and record their reason.
- Before creating a scheduled task, use list_task_handlers and choose a valid handler name.
- You understand ANY language. Parse the user's intent regardless of what language they write in.
- Be concise in responses. Don't over-explain.
- If you're unsure what the user wants, ask for clarification.
- Always confirm back what action you took after executing a tool."""


class AIInterface:
    """Conversational AI controller with tool-use.

    Receives raw user messages, uses LLM with tool-calling to understand
    intent and execute actions, returns a text response.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        store: Store,
        bus: AsyncIOBus,
        portfolio: PortfolioTracker,
        scheduler: Scheduler,
    ) -> None:
        self._registry = registry
        self._store = store
        self._bus = bus
        self._portfolio = portfolio
        self._scheduler = scheduler
        self._conversation_history: dict[str, list[dict]] = self._store.load_conversation_history()

    def _append_message(self, channel_id: str, role: str, content: str) -> None:
        """Append a message to in-memory and persisted history."""
        self._conversation_history.setdefault(channel_id, []).append({
            "role": role,
            "content": content,
        })
        self._store.append_conversation_message(channel_id, role, content)

    async def handle_message(
        self,
        text: str,
        channel_id: str = "default",
        source: str = "unknown",
    ) -> str:
        """Process a user message and return a response.

        This is the main entry point. Integrations call this with raw
        user text and get back a response to display.
        """
        # Get or create conversation history for this channel
        history = self._conversation_history.setdefault(channel_id, [])

        # Add user message
        self._append_message(channel_id, "user", text)
        history = self._conversation_history[channel_id]

        # Get LLM provider
        providers = self._registry.get_all("llm")
        if not providers:
            return "No AI provider configured. Please set up an LLM provider in config.yaml."

        llm: LLMProvider = providers[0]

        # Build messages with system prompt
        system_prompt = SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_prompt}] + history
        available_tools = TOOLS + self._collect_plugin_tools()

        # Call LLM with tools
        try:
            result = await llm.tool_call(messages, available_tools)
        except Exception:
            logger.exception("LLM call failed")
            return "Sorry, I couldn't process that right now. Please try again."

        # Process tool calls if any
        if result.has_tool_calls:
            tool_results = []
            for tc in result.tool_calls:
                func = tc.get("function", tc)
                tool_name = func.get("name", "")
                args = func.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                tool_result = await self._execute_tool(tool_name, args, source, channel_id)
                tool_results.append(f"[{tool_name}]: {tool_result}")

            # Add tool results to conversation and get final response
            tool_summary = "\n".join(tool_results)
            self._append_message(channel_id, "assistant", result.text or "")
            self._append_message(
                channel_id,
                "user",
                f"Tool results:\n{tool_summary}\n\nSummarize what happened for the user.",
            )
            history = self._conversation_history[channel_id]

            try:
                final_response = await llm.complete(
                    [{"role": "system", "content": system_prompt}] + history
                )
            except Exception:
                logger.exception("Final response generation failed")
                final_response = tool_summary

            self._append_message(channel_id, "assistant", final_response)
            return final_response
        else:
            # No tool calls -- direct response
            response = result.text or "I'm not sure how to help with that."
            self._append_message(channel_id, "assistant", response)
            return response

    async def handle_scheduled_prompt(
        self,
        prompt: str,
        channel_id: str = "default",
        source: str = "scheduler",
        persist_output: bool = True,
    ) -> str:
        """Run one stateless AI turn with the same tools/system prompt.

        This is used by cron-triggered tasks. It does not use prior conversation
        history; it starts from one user prompt. Optionally persists only the
        final assistant response into the target channel conversation.
        """
        providers = self._registry.get_all("llm")
        if not providers:
            return "No AI provider configured. Please set up an LLM provider in config.yaml."

        llm: LLMProvider = providers[0]
        system_prompt = SYSTEM_PROMPT
        history: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        messages = [{"role": "system", "content": system_prompt}] + history
        available_tools = TOOLS + self._collect_plugin_tools()

        try:
            result = await llm.tool_call(messages, available_tools)
        except Exception:
            logger.exception("Scheduled LLM call failed")
            return "Sorry, I couldn't process that scheduled run right now."

        if result.has_tool_calls:
            tool_results = []
            for tc in result.tool_calls:
                func = tc.get("function", tc)
                tool_name = func.get("name", "")
                args = func.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_result = await self._execute_tool(tool_name, args, source, channel_id)
                tool_results.append(f"[{tool_name}]: {tool_result}")

            tool_summary = "\n".join(tool_results)
            history.append({"role": "assistant", "content": result.text or ""})
            history.append({
                "role": "user",
                "content": f"Tool results:\n{tool_summary}\n\nSummarize what happened for the user.",
            })
            try:
                final_response = await llm.complete(
                    [{"role": "system", "content": system_prompt}] + history
                )
            except Exception:
                logger.exception("Scheduled final response generation failed")
                final_response = tool_summary
        else:
            final_response = result.text or "I'm not sure how to help with that."

        if persist_output and final_response:
            self._append_message(channel_id, "assistant", final_response)

        return final_response

    async def _execute_tool(self, name: str, args: dict, source: str, channel_id: str = "default") -> str:
        """Execute a tool call and return a result string."""
        try:
            match name:
                case "confirm_trade":
                    return await self._tool_confirm_trade(args, source)
                case "skip_trade":
                    return await self._tool_skip_trade(args, source)
                case "close_position":
                    return await self._tool_close_position(args, source)
                case "user_initiated_trade":
                    return await self._tool_user_initiated(args, source)
                case "get_portfolio":
                    return self._tool_get_portfolio(args)
                case "get_price":
                    return await self._tool_get_price(args)
                case "list_tasks":
                    return self._tool_list_tasks()
                case "list_task_handlers":
                    return self._tool_list_task_handlers()
                case "create_task":
                    return await self._tool_create_task(args, channel_id=channel_id, source=source)
                case "delete_task":
                    return await self._tool_delete_task(args)
                case "get_memories":
                    return self._tool_get_memories(args)
                case "get_signals":
                    return self._tool_get_signals(args)
                case "run_analysis":
                    return await self._tool_run_analysis(args)
                case _:
                    plugin_result = await self._execute_plugin_tool(
                        name,
                        args,
                        source,
                        channel_id=channel_id,
                    )
                    if plugin_result is not None:
                        return plugin_result
                    return f"Unknown tool: {name}"
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return f"Error executing {name}: {e}"

    def _iter_plugins(self) -> list[Any]:
        """Return registered plugin instances across protocol registries."""
        protocol_keys = ("market_data", "input", "output", "llm", "agent", "risk_rule", "task_handler")
        seen: set[int] = set()
        plugins: list[Any] = []
        for key in protocol_keys:
            try:
                items = self._registry.get_all(key)
            except KeyError:
                continue
            for item in items:
                item_id = id(item)
                if item_id in seen:
                    continue
                seen.add(item_id)
                plugins.append(item)
        return plugins

    def _collect_plugin_tools(self) -> list[dict]:
        """Collect tool schemas exposed by plugins via get_tools()."""
        tools: list[dict] = []
        existing_names = {
            t.get("function", {}).get("name")
            for t in TOOLS
            if isinstance(t, dict)
        }
        for plugin in self._iter_plugins():
            get_tools = getattr(plugin, "get_tools", None)
            if get_tools is None:
                continue
            try:
                plugin_tools = get_tools() or []
            except Exception:
                logger.exception("Plugin %s get_tools() failed", getattr(plugin, "name", "?"))
                continue
            for tool in plugin_tools:
                if not isinstance(tool, dict):
                    continue
                name = tool.get("function", {}).get("name")
                if not name or name in existing_names:
                    continue
                tools.append(tool)
                existing_names.add(name)
        return tools

    async def _execute_plugin_tool(
        self,
        name: str,
        args: dict,
        source: str,
        channel_id: str = "default",
    ) -> str | None:
        """Attempt to execute a plugin-provided tool via call_tool()."""
        for plugin in self._iter_plugins():
            call_tool = getattr(plugin, "call_tool", None)
            if call_tool is None:
                continue
            try:
                try:
                    result = call_tool(
                        name=name,
                        args=args,
                        source=source,
                        channel_id=channel_id,
                        interface=self,
                    )
                except TypeError:
                    result = call_tool(name, args)
                if isawaitable(result):
                    result = await result
            except Exception:
                logger.exception("Plugin %s call_tool() failed for %s", getattr(plugin, "name", "?"), name)
                continue
            if result is not None:
                return str(result)
        return None

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_confirm_trade(self, args: dict, source: str) -> str:
        ticker = args["ticker"].upper()
        price = args["entry_price"]
        size = args.get("size")

        # Find the most recent signal for this ticker
        signals = self._store.list_json("signals", Signal)
        matching = [s for s in signals if s.ticker == ticker and s.status in ("approved", "delivered")]

        if matching:
            signal = matching[-1]  # most recent
            pos = self._portfolio.human_confirm_position(
                signal=signal, entry_price=price, size=size, via=source,
            )
            await self._bus.publish(Event(
                type=EventTypes.POSITION_CONFIRMED,
                source="interface",
                payload={"ticker": ticker, "price": price, "portfolio": "human"},
            ))
            return f"Confirmed: {ticker} position opened at ${price:,.2f}" + (f" ({size} units)" if size else "")
        else:
            # No matching signal -- treat as user-initiated
            return await self._tool_user_initiated(
                {"ticker": ticker, "direction": "long", "entry_price": price, "size": size},
                source,
            )

    async def _tool_skip_trade(self, args: dict, source: str) -> str:
        ticker = args["ticker"].upper()
        reason = args.get("reason", "")

        signals = self._store.list_json("signals", Signal)
        matching = [s for s in signals if s.ticker == ticker and s.status in ("approved", "delivered")]

        if matching:
            signal = matching[-1]
            self._portfolio.human_skip_position(signal=signal, via=source, notes=reason)
            await self._bus.publish(Event(
                type=EventTypes.POSITION_SKIPPED,
                source="interface",
                payload={"ticker": ticker, "reason": reason},
            ))
            return f"Skipped: {ticker} signal." + (f" Reason: {reason}" if reason else "")
        else:
            return f"No pending signal found for {ticker}."

    async def _tool_close_position(self, args: dict, source: str) -> str:
        ticker = args["ticker"].upper()
        close_price = args["close_price"]

        pos = self._portfolio.human_close_position(ticker, close_price, via=source)
        if pos:
            pnl = pos.realized_pnl or 0
            pct = pos.realized_pnl_percent or 0
            await self._bus.publish(Event(
                type=EventTypes.POSITION_UPDATED,
                source="interface",
                payload={"ticker": ticker, "action": "closed", "price": close_price},
            ))
            return f"Closed: {ticker} at ${close_price:,.2f}. P&L: ${pnl:,.2f} ({pct:+.1f}%)"
        return f"No open position found for {ticker} in human portfolio."

    async def _tool_user_initiated(self, args: dict, source: str) -> str:
        ticker = args["ticker"].upper()
        direction = args.get("direction", "long")
        price = args["entry_price"]
        size = args.get("size")
        reason = args.get("reason", "User-initiated trade")

        self._portfolio.human_initiated_trade(
            ticker=ticker, direction=direction, entry_price=price,
            size=size, via=source, notes=reason,
        )
        await self._bus.publish(Event(
            type=EventTypes.POSITION_CONFIRMED,
            source="interface",
            payload={"ticker": ticker, "price": price, "user_initiated": True},
        ))
        return f"Recorded: {direction} {ticker} at ${price:,.2f}" + (f" ({size} units)" if size else "") + f". Reason: {reason}"

    def _tool_get_portfolio(self, args: dict) -> str:
        portfolio_type = args.get("portfolio_type", "both")
        parts = []

        if portfolio_type in ("ai", "both"):
            summary = self._portfolio.get_summary("ai")
            parts.append(f"AI Portfolio: {len(summary.positions)} positions, P&L: {summary.total_pnl_percent:+.1f}%")
            for p in summary.positions:
                pnl_str = f" P&L: {p.pnl_percent:+.1f}%" if p.pnl_percent else ""
                parts.append(f"  {p.direction} {p.ticker} @ ${p.entry_price:,.2f}{pnl_str} [{p.status}]")

        if portfolio_type in ("human", "both"):
            summary = self._portfolio.get_summary("human")
            parts.append(f"Human Portfolio: {len(summary.positions)} positions, P&L: {summary.total_pnl_percent:+.1f}%")
            for p in summary.positions:
                pnl_str = f" P&L: {p.pnl_percent:+.1f}%" if p.pnl_percent else ""
                parts.append(f"  {p.direction} {p.ticker} @ ${p.entry_price:,.2f}{pnl_str} [{p.status}]")

        return "\n".join(parts) if parts else "No positions in either portfolio."

    async def _tool_get_price(self, args: dict) -> str:
        ticker = args["ticker"].upper()
        price = self._store.get_latest_price(ticker)
        if price:
            return f"{ticker}: ${price:,.2f}"

        # Fallback: fetch live data from market providers when cache is empty.
        providers = self._registry.get_all("market_data")
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)

        candidates = [ticker]
        if "-" not in ticker and "=" not in ticker and not ticker.startswith("^"):
            candidates.extend([f"{ticker}-USD", f"{ticker}=X"])

        for provider in providers:
            for candidate in candidates:
                if not provider.supports(candidate):
                    continue
                try:
                    rows = await provider.fetch([candidate], start=start, end=now)
                except Exception:
                    logger.exception("Live price fetch failed for %s via %s", candidate, provider.name)
                    continue
                if not rows:
                    continue

                self._store.save_market_data(rows)
                latest = max(rows, key=lambda r: r.timestamp)
                return f"{latest.ticker}: ${latest.close:,.2f}"

        return f"No price data available for {ticker}. No live quote returned from configured market data providers."

    def _tool_list_task_handlers(self) -> str:
        handlers = sorted(self._registry.names("task_handler"))
        if not handlers:
            return "No task handlers are currently registered."
        return "Available task handlers:\n" + "\n".join(f"  - {name}" for name in handlers)

    def _tool_list_tasks(self) -> str:
        tasks = self._scheduler.list_tasks()
        if not tasks:
            return "No scheduled tasks."

        parts = []
        for t in tasks:
            status = "enabled" if t.enabled else "disabled"
            schedule = t.cron_expression or (t.run_at.isoformat() if t.run_at else "immediate")
            parts.append(f"  [{t.id}] {t.name} ({t.type}, {status}) schedule: {schedule} by: {t.created_by}")
        return f"{len(tasks)} tasks:\n" + "\n".join(parts)

    async def _tool_create_task(
        self,
        args: dict,
        channel_id: str = "default",
        source: str = "unknown",
    ) -> str:
        handler = args["handler"]
        if not self._registry.has("task_handler", handler):
            available = sorted(self._registry.names("task_handler"))
            if available:
                return (
                    f"Cannot create task. Unknown handler '{handler}'. "
                    f"Use one of: {', '.join(available)}"
                )
            return f"Cannot create task. Unknown handler '{handler}' and no handlers are registered."

        params = args.get("params", {})
        if not isinstance(params, dict):
            params = {}

        # Default to the same conversation channel where the task was created.
        params.setdefault("channel_id", channel_id)
        output_names = set(self._registry.names("output"))
        if source in output_names:
            params.setdefault("adapter", source)

        task = Task(
            name=args["name"],
            type=args.get("type", "recurring"),
            handler=handler,
            cron_expression=args.get("cron_expression"),
            params=params,
            created_by="ai",
        )
        if args.get("run_at"):
            task.run_at = datetime.fromisoformat(args["run_at"])

        await self._scheduler.create_task(task)
        return f"Created task: {task.name} ({task.type}, handler: {task.handler})"

    async def _tool_delete_task(self, args: dict) -> str:
        task_id = args["task_id"]
        deleted = await self._scheduler.delete_task(task_id)
        return f"Deleted task {task_id}" if deleted else f"Task {task_id} not found"

    def _tool_get_memories(self, args: dict) -> str:
        ticker = args.get("ticker")
        limit = args.get("limit", 10)

        if ticker:
            memory_ids = self._store.search_memories(ticker=ticker.upper(), limit=limit)
        else:
            memory_ids = self._store.search_memories(limit=limit)

        if not memory_ids:
            return "No memories found."

        parts = []
        for mid in memory_ids:
            mem = self._store.read_json("memories", f"{mid}.json", Memory)
            if mem:
                parts.append(
                    f"  [{mem.who_was_right} was right] {mem.ai_action} vs {mem.human_action}\n"
                    f"    Lesson: {mem.lesson[:150]}"
                )
        return f"{len(parts)} memories:\n" + "\n".join(parts)

    def _tool_get_signals(self, args: dict) -> str:
        signals = self._store.list_json("signals", Signal)
        status_filter = args.get("status")
        limit = args.get("limit", 10)

        if status_filter:
            signals = [s for s in signals if s.status == status_filter]

        signals = signals[-limit:]  # most recent

        if not signals:
            return "No signals found."

        parts = []
        for s in signals:
            parts.append(f"  [{s.status}] {s.direction.upper()} {s.ticker} conf={s.confidence:.0%} ({s.created_at.strftime('%Y-%m-%d')})")
        return f"{len(parts)} signals:\n" + "\n".join(parts)

    async def _tool_run_analysis(self, args: dict) -> str:
        topic = args["topic"]

        # Publish an event that the orchestrator can pick up
        event = Event(
            type=EventTypes.INTEGRATION_INPUT,
            source="interface",
            payload={
                "text": f"Analyze: {topic}",
                "ticker": topic.upper() if len(topic) <= 10 else None,
                "priority": "high",
                "requested_by": "user",
            },
        )
        await self._bus.publish(event)
        return f"Analysis requested for: {topic}. Results will be delivered when ready."

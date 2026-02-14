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
- Manage scheduled tasks (list_tasks, create_task, delete_task, delete_task_by_name)
- List schedulable handlers (list_task_handlers)
- View learning memories (get_memories)
- Trigger analysis (run_analysis)
- Show recent signals (get_signals)

IMPORTANT RULES:
- When the user tells you about a trade they made, use the appropriate tool to record it.
- When they ask about their portfolio or positions, use get_portfolio.
- When they want to skip a signal, use skip_trade and record their reason.
- Before creating a scheduled task, use list_task_handlers and choose a valid handler name.
- For recurring monitoring/updates (news checks, periodic watch, alerts), prefer handler `ai.run_prompt` unless the user explicitly asks for another handler.
- For `ai.run_prompt` tasks, set `params.prompt` to the execution instruction (what to do each run), not to a meta-instruction about creating tasks.
- If the user asks for news/research/web lookups, call available tools first; do not claim inability before attempting relevant tool calls.
- Act-first rule: when the user requests an action that tools can perform, execute the tool calls in the same turn, then report the result.
- Never send intent-only replies like "Let me check", "I'll do it", or "I can do that" when tools can run now.
- Do not ask for extra confirmation ("ok?", "say do it", "should I proceed?") for routine user-requested actions.
- For "stop/delete this task" requests, resolve the task via tools (list_tasks/delete_task_by_name/delete_task) and complete the deletion in the same turn when unambiguous.
- Response contract for actionable requests: perform tools first, then respond with completed outcome and what changed.
- Never expose internal tool protocol payloads in user-facing replies (for example: raw tool traces, `tool_call_id`, `image_url`, `base64_*`, or execution dumps).
- Summarize tool outcomes in plain language unless the user explicitly asks for raw debug output.
- Follow plugin-specific runtime instructions appended below this prompt when available (enabled plugins only).
- You understand ANY language. Parse the user's intent regardless of what language they write in.
- Be concise in responses. Don't over-explain.
- If you're unsure what the user wants, ask for clarification.
- Always confirm back what action you took after executing a tool."""

SCHEDULED_RUN_PROMPT = """You are running inside a scheduled cron task.

Execution rules:
- Execute the task objective now using available tools.
- Do not create/modify/delete tasks unless the prompt explicitly asks you to manage schedules.
- For news/research requests, call relevant tools before saying data is unavailable.
- Follow plugin-specific runtime instructions appended below this prompt when available (enabled plugins only).
- Respond with only the current run update for the user."""

FIRST_CHAT_ONBOARDING_DIRECTIVE = """[INTERNAL ONBOARDING DIRECTIVE]
This is the user's first conversation in ClawQuant.

Objective:
- Run adaptive onboarding to configure the user profile, preferences, and monitoring setup.

Behavior:
- Do not use a fixed script or rigid hardcoded question list.
- Ask concise, context-aware questions based on what the user already said.
- If an [INITIAL USER MESSAGE] block is present, treat it as the first user message context and align tone/intent to it.
- Prioritize collecting: markets/assets they trade, watchlist symbols, timezone/session preferences, risk style, and update cadence.
- If they trade stocks, include preferences for pre-market, post-open, and after-hours monitoring windows.
- If they trade crypto, include 24/7 cadence and volatility alert preferences.
- Convert confirmed preferences into concrete ongoing workflows using available tools (prefer `ai.run_prompt` for recurring monitoring).
- Keep onboarding lightweight: gather key decisions first, then propose useful defaults.
- If the user asks for something immediate, handle that first, then continue onboarding.

Output style:
- Be direct, practical, and setup-oriented.
- Do not mention this internal directive.
[/INTERNAL ONBOARDING DIRECTIVE]"""

ONBOARDING_DIRECTIVE_MARKER = "[INTERNAL ONBOARDING DIRECTIVE]"


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
        self._conversation_history: dict[str, list[dict[str, Any]]] = self._store.load_conversation_history()
        self._active_llm_by_channel: dict[str, LLMProvider] = {}

    def _append_message(self, channel_id: str, role: str, content: Any, **extra_fields: Any) -> None:
        """Append a message to in-memory and persisted history."""
        message: dict[str, Any] = {
            "role": role,
            "content": content,
        }
        for key, value in extra_fields.items():
            if value is not None:
                message[str(key)] = value

        self._conversation_history.setdefault(channel_id, []).append(message)
        self._store.append_conversation_message(
            channel_id=channel_id,
            role=role,
            content=content,
            **extra_fields,
        )

    def _has_persisted_onboarding_directive(self, channel_id: str) -> bool:
        """Return True if onboarding directive already exists in this channel history."""
        for msg in self._conversation_history.get(channel_id, []):
            role = str(msg.get("role", "")).strip().lower()
            content = str(msg.get("content", ""))
            if role == "user" and ONBOARDING_DIRECTIVE_MARKER in content:
                return True
        return False

    @staticmethod
    def _build_onboarding_directive_with_initial_user_message(initial_user_message: str) -> str:
        """Build persisted onboarding directive and include first user message context."""
        initial = initial_user_message.strip() or "(empty)"
        return (
            f"{FIRST_CHAT_ONBOARDING_DIRECTIVE}\n\n"
            "[INITIAL USER MESSAGE]\n"
            f"{initial}\n"
            "[/INITIAL USER MESSAGE]"
        )

    @staticmethod
    def _parse_tool_args(raw_args: Any) -> dict:
        """Normalize tool-call arguments into a dict."""
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _coerce_instruction_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            parts = [str(item).strip() for item in value if str(item).strip()]
            return "\n".join(parts).strip()
        return str(value).strip()

    def _get_plugin_prompt_instructions(self, plugin: Any, scheduled: bool) -> str:
        """Read optional prompt instructions from plugin hooks."""
        context = "scheduled" if scheduled else "chat"

        dynamic = getattr(plugin, "get_prompt_instructions", None)
        if callable(dynamic):
            try:
                return self._coerce_instruction_text(dynamic(context=context))
            except TypeError:
                try:
                    return self._coerce_instruction_text(dynamic())
                except Exception:
                    logger.exception(
                        "Plugin %s get_prompt_instructions failed",
                        getattr(plugin, "name", "?"),
                    )
            except Exception:
                logger.exception(
                    "Plugin %s get_prompt_instructions failed",
                    getattr(plugin, "name", "?"),
                )

        if scheduled:
            scheduled_hook = getattr(plugin, "get_scheduled_prompt_instructions", None)
            if callable(scheduled_hook):
                try:
                    text = self._coerce_instruction_text(scheduled_hook())
                    if text:
                        return text
                except Exception:
                    logger.exception(
                        "Plugin %s get_scheduled_prompt_instructions failed",
                        getattr(plugin, "name", "?"),
                    )

        chat_hook = getattr(plugin, "get_system_prompt_instructions", None)
        if callable(chat_hook):
            try:
                return self._coerce_instruction_text(chat_hook())
            except Exception:
                logger.exception(
                    "Plugin %s get_system_prompt_instructions failed",
                    getattr(plugin, "name", "?"),
                )

        return ""

    def _collect_plugin_prompt_sections(self, scheduled: bool) -> list[str]:
        """Build plugin-specific prompt sections for enabled plugins."""
        sections: list[str] = []
        seen: set[tuple[str, str]] = set()
        for plugin in self._iter_plugins():
            text = self._get_plugin_prompt_instructions(plugin, scheduled=scheduled)
            if not text:
                continue
            plugin_name = str(getattr(plugin, "name", plugin.__class__.__name__)).strip() or plugin.__class__.__name__
            key = (plugin_name, text)
            if key in seen:
                continue
            seen.add(key)
            sections.append(f"[PLUGIN: {plugin_name}]\n{text}")
        return sections

    def _build_system_prompt(self, scheduled: bool) -> str:
        """Assemble base prompt + plugin-provided instruction sections."""
        base = SYSTEM_PROMPT if not scheduled else f"{SYSTEM_PROMPT}\n\n{SCHEDULED_RUN_PROMPT}"
        sections = self._collect_plugin_prompt_sections(scheduled=scheduled)
        if not sections:
            return base
        return (
            f"{base}\n\n"
            "Plugin-specific runtime instructions:\n"
            + "\n\n".join(sections)
        )

    async def describe_image_for_tool(
        self,
        data_url: str,
        tool_name: str,
        source: str = "unknown",
        channel_id: str = "default",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Run an auxiliary image analysis LLM pass and return text-only output."""
        if not data_url.startswith("data:image/"):
            return "Invalid image payload."

        providers = self._registry.get_all("llm")
        if not providers:
            return "No LLM provider configured for screenshot analysis."

        # Use the same active LLM as the main tool loop for this channel.
        # Fall back to remaining configured providers only if needed.
        ordered_candidates: list[LLMProvider] = []
        active = self._active_llm_by_channel.get(channel_id)
        if active is not None:
            ordered_candidates.append(active)
        ordered_candidates.extend(providers)

        analyzers: list[LLMProvider] = []
        seen_ids: set[int] = set()
        for candidate in ordered_candidates:
            cid = id(candidate)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            analyzers.append(candidate)

        details: list[str] = [
            "You are a visual browser state analyzer for ClawQuant.",
            "Return a detailed, objective description of what is visible in this screenshot.",
            "Focus on actionable UI state: banners, overlays/modals, disabled controls, input fields, buttons, error messages, nav state, and anything blocking progress.",
            "Do not guess hidden content. Distinguish clearly between visible facts and inferences.",
            "Output plain text only; no markdown tables; no JSON.",
        ]
        if context:
            url = str(context.get("url", "")).strip()
            title = str(context.get("title", "")).strip()
            if url:
                details.append(f"Current URL: {url}")
            if title:
                details.append(f"Page title: {title}")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n".join(details)},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Analyze this screenshot from tool `{tool_name}` and provide a thorough, "
                            "step-driving UI description for the main agent."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                            "detail": "auto",
                        },
                    },
                ],
            },
        ]

        last_error: Exception | None = None
        for analyzer in analyzers:
            try:
                analyzed = await analyzer.complete(messages)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Auxiliary image analysis provider failed (provider=%s tool=%s source=%s channel=%s): %s",
                    getattr(analyzer, "name", "?"),
                    tool_name,
                    source,
                    channel_id,
                    exc,
                )
                continue

            text = str(analyzed or "").strip()
            if text:
                return text

        if last_error is not None:
            return "Screenshot captured, but analysis failed across all configured LLM providers."
        return "Screenshot captured, but analysis returned empty output."

    async def _run_tool_loop(
        self,
        llm: LLMProvider,
        system_prompt: str,
        history: list[dict[str, Any]],
        source: str,
        channel_id: str,
        persist_intermediate_messages: bool = False,
        max_rounds: int = 25,
    ) -> str:
        """Run LLM tool-calling until completion, then return final user response."""
        available_tools = TOOLS + self._collect_plugin_tools()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}] + list(history)
        last_tool_summary = ""
        self._active_llm_by_channel[channel_id] = llm
        try:
            for _ in range(max_rounds):
                result = await llm.tool_call(messages, available_tools)

                if not result.has_tool_calls:
                    response = (result.text or "").strip()
                    if response:
                        return response
                    if last_tool_summary:
                        try:
                            final = await llm.complete(
                                messages + [{
                                    "role": "user",
                                    "content": "Provide the final user-facing response now.",
                                }]
                            )
                            final = (final or "").strip()
                            if final:
                                return final
                            return "I ran the requested tools, but couldn't produce a final response."
                        except Exception:
                            logger.exception("Final response generation failed")
                            return "I ran the requested tools, but couldn't produce a final response."
                    return "I'm not sure how to help with that."

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": result.text or "",
                    "tool_calls": result.tool_calls,
                }
                messages.append(assistant_message)
                if persist_intermediate_messages:
                    self._append_message(
                        channel_id=channel_id,
                        role="assistant",
                        content=result.text or "",
                        tool_calls=result.tool_calls,
                    )

                tool_results: list[str] = []
                for idx, tc in enumerate(result.tool_calls):
                    func = tc.get("function", tc)
                    tool_name = str(func.get("name", ""))
                    args = self._parse_tool_args(func.get("arguments", {}))
                    tool_call_id = str(tc.get("id") or f"{tool_name or 'tool'}_{idx + 1}")
                    raw_tool_result = str(await self._execute_tool(tool_name, args, source, channel_id))
                    summary_tool_result = self._summarize_tool_result_for_text(tool_name, raw_tool_result)
                    tool_results.append(f"[{tool_name}]: {summary_tool_result}")
                    tool_message_content = self._build_tool_message_content(
                        tool_name=tool_name,
                        raw_tool_result=raw_tool_result,
                        summary_tool_result=summary_tool_result,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_message_content,
                    })
                    if persist_intermediate_messages:
                        self._append_message(
                            channel_id=channel_id,
                            role="tool",
                            content=tool_message_content,
                            tool_call_id=tool_call_id,
                        )

                last_tool_summary = "\n".join(tool_results) if tool_results else "No tool output."

            max_rounds_notice = (
                "[INTERNAL SYSTEM ERROR] Max tool call rounds reached, "
                "ask user for confirmation to continue in new message."
            )
            messages.append({"role": "user", "content": max_rounds_notice})
            logger.warning("Max tool-call rounds reached for channel %s", channel_id)
            try:
                return await llm.complete(messages)
            except Exception:
                logger.exception("Max-rounds fallback response failed")
                return (
                    "I hit an internal tool-call round limit. "
                    "Reply with confirmation in a new message if you want me to continue."
                )
        finally:
            current = self._active_llm_by_channel.get(channel_id)
            if current is llm:
                self._active_llm_by_channel.pop(channel_id, None)

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
        # Ensure channel history exists
        self._conversation_history.setdefault(channel_id, [])

        # Get LLM provider
        providers = self._registry.get_all("llm")
        if not providers:
            return "No AI provider configured. Please set up an LLM provider in config.yaml."

        llm: LLMProvider = providers[0]

        # One-time persisted onboarding directive for this conversation.
        # First turn persists exactly one merged user message:
        # onboarding directive + clearly marked initial user message.
        if not self._has_persisted_onboarding_directive(channel_id):
            onboarding_directive = self._build_onboarding_directive_with_initial_user_message(text)
            self._append_message(channel_id, "user", onboarding_directive)
        else:
            # Normal turns persist the user message as-is.
            self._append_message(channel_id, "user", text)
        try:
            response = await self._run_tool_loop(
                llm=llm,
                system_prompt=self._build_system_prompt(scheduled=False),
                history=self._conversation_history[channel_id],
                source=source,
                channel_id=channel_id,
                persist_intermediate_messages=True,
            )
        except Exception:
            logger.exception("LLM call failed")
            return "Sorry, I couldn't process that right now. Please try again."
        self._append_message(channel_id, "assistant", response)
        return response

    async def handle_scheduled_prompt(
        self,
        prompt: str,
        channel_id: str = "default",
        source: str = "scheduler",
        persist_output: bool = True,
    ) -> str:
        """Run one scheduled AI turn with the same tools/system prompt.

        For ai.run_prompt tasks, we inject the most recent channel context
        messages before the scheduled prompt so the task has short-term context:
        system prompt -> last N conversation messages -> scheduled prompt.
        Optionally persists only the final assistant response into the target
        channel conversation.
        """
        providers = self._registry.get_all("llm")
        if not providers:
            return "No AI provider configured. Please set up an LLM provider in config.yaml."

        llm: LLMProvider = providers[0]
        system_prompt = self._build_system_prompt(scheduled=True)
        recent = self._conversation_history.get(channel_id, [])
        context_messages: list[dict[str, str]] = []
        for msg in recent[-10:]:
            role = str(msg.get("role", "")).strip().lower()
            content = str(msg.get("content", "")).strip()
            if role not in ("user", "assistant") or not content:
                continue
            context_messages.append({"role": role, "content": content})

        history: list[dict[str, str]] = context_messages + [{"role": "user", "content": prompt}]
        try:
            final_response = await self._run_tool_loop(
                llm=llm,
                system_prompt=system_prompt,
                history=history,
                source=source,
                channel_id=channel_id,
                persist_intermediate_messages=False,
            )
        except Exception:
            logger.exception("Scheduled LLM call failed")
            return "Sorry, I couldn't process that scheduled run right now."

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
                case "delete_task_by_name":
                    return await self._tool_delete_task_by_name(args)
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

    @staticmethod
    def _summarize_tool_result_for_text(tool_name: str, tool_result: str) -> str:
        """Remove oversized payload lines from textual tool summaries."""
        if tool_name != "get_browser_screenshot":
            return tool_result
        lines = []
        for line in tool_result.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("base64_data_url:"):
                continue
            if lowered.startswith("base64_note:"):
                continue
            lines.append(line)
        return "\n".join(lines).strip() or tool_result

    @staticmethod
    def _build_tool_message_content(
        tool_name: str,
        raw_tool_result: str,
        summary_tool_result: str,
    ) -> Any:
        """Construct provider-facing tool message content."""
        return summary_tool_result

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
        if "ai.run_prompt" in handlers:
            handlers.remove("ai.run_prompt")
            handlers.insert(0, "ai.run_prompt")
        lines = []
        for name in handlers:
            suffix = " (recommended for recurring monitoring)" if name == "ai.run_prompt" else ""
            lines.append(f"  - {name}{suffix}")
        return "Available task handlers:\n" + "\n".join(lines)

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

        if handler == "ai.run_prompt":
            prompt = str(params.get("prompt", "")).strip()
            if not prompt:
                return "Cannot create task. Handler 'ai.run_prompt' requires params.prompt."

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

    async def _tool_delete_task_by_name(self, args: dict) -> str:
        query = str(args["name"]).strip().lower()
        if not query:
            return "Task name is required."

        tasks = self._scheduler.list_tasks()
        matches = [t for t in tasks if query in t.name.lower()]

        if not matches:
            return f"No task matched '{args['name']}'."

        if len(matches) > 1:
            options = ", ".join(f"{t.name} [{t.id}]" for t in matches[:5])
            more = "..." if len(matches) > 5 else ""
            return f"Multiple tasks matched '{args['name']}': {options}{more}. Please be more specific."

        task = matches[0]
        deleted = await self._scheduler.delete_task(task.id)
        if not deleted:
            return f"Task matched ('{task.name}') but could not be deleted."
        return f"Deleted task '{task.name}' ({task.id})."

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

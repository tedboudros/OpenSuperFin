"""Selenium-backed browser tools for the AI interface.

This plugin is intentionally optional and dependency-heavy. It is only loaded
when enabled in scheduler.handlers and after selenium is installed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import threading
from contextlib import redirect_stdout
from html.parser import HTMLParser
from typing import Any

from core.models.tasks import TaskResult

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "selenium_browser",
    "display_name": "Selenium Browser",
    "description": "Browser automation tools (open/close, execute selenium code, screenshot, page digest)",
    "category": "task_handler",
    "protocols": ["task_handler"],
    "class_name": "SeleniumBrowserHandler",
    "pip_dependencies": ["selenium>=4.20.0"],
    "auto_enable": False,
    "setup_hook": "setup_selenium_config",
    "setup_instructions": """
Optional browser automation tools using Selenium.

Notes:
- Requires local browser availability (Chrome/Firefox) and matching driver support.
- In headless/server environments, prefer headless mode.
- This plugin is loaded only when enabled.
- Setup can store named login profiles (username/password) as encrypted-ish env blob.
""",
    "config_fields": [
        {
            "key": "default_browser",
            "label": "Default Browser",
            "type": "choice",
            "required": False,
            "default": "chrome",
            "choices": ["chrome", "firefox"],
            "description": "Browser engine for open_browser when not specified",
        },
        {
            "key": "headless",
            "label": "Headless Mode",
            "type": "boolean",
            "required": False,
            "default": True,
            "description": "Start browser without visible UI by default",
        },
        {
            "key": "page_code_max_chars",
            "label": "Default Page Digest Max Chars",
            "type": "number",
            "required": False,
            "default": 6000,
            "description": "Max output size returned by get_page_code",
            "placeholder": "6000",
        },
        {
            "key": "logins_b64",
            "label": "Saved Login Profiles",
            "type": "secret",
            "required": False,
            "env_var": "SELENIUM_LOGINS_B64",
            "hidden": True,
            "description": "Base64-encoded JSON list of login profiles",
        },
    ],
}


class _SlimHTMLParser(HTMLParser):
    """Collect compressed text and links from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._active_href: str | None = None
        self._active_link_parts: list[str] = []
        self.text_chunks: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if lowered == "a":
            attrs_map = dict(attrs)
            href = (attrs_map.get("href") or "").strip()
            self._active_href = href if href else None
            self._active_link_parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if lowered == "a":
            if self._active_href:
                link_text = _squash(" ".join(self._active_link_parts))
                self.links.append((link_text, self._active_href))
            self._active_href = None
            self._active_link_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        cleaned = _squash(data)
        if not cleaned:
            return
        self.text_chunks.append(cleaned)
        if self._active_href is not None:
            self._active_link_parts.append(cleaned)


def _squash(text: str, max_len: int = 220) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


def _dedupe_text(values: list[str], max_items: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        norm = value.strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(value.strip())
        if len(out) >= max_items:
            break
    return out


def _dedupe_links(values: list[tuple[str, str]], max_items: int) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for text, href in values:
        href_clean = href.strip()
        if not href_clean:
            continue
        key = href_clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((_squash(text or href_clean, 120), href_clean))
        if len(out) >= max_items:
            break
    return out


class SeleniumBrowserHandler:
    """Tool-hosting task handler for Selenium browser automation."""

    def __init__(
        self,
        default_browser: str = "chrome",
        headless: bool = True,
        page_code_max_chars: int = 6000,
        logins_b64: str = "",
    ) -> None:
        self._default_browser = (default_browser or "chrome").strip().lower()
        self._headless = bool(headless)
        self._page_code_max_chars = int(page_code_max_chars)
        self._driver: Any | None = None
        self._lock = threading.RLock()
        if not logins_b64:
            logins_b64 = os.environ.get("SELENIUM_LOGINS_B64", "")
        self._login_profiles = self._decode_login_profiles(logins_b64)

    @property
    def name(self) -> str:
        return "browser.selenium"

    def get_system_prompt_instructions(self) -> str:
        """Detailed Selenium operating instructions injected into system prompt."""
        return (
            "Selenium Browser Playbook:\n"
            "- Work incrementally with many short tool calls, not one long script.\n"
            "- Before any browser interaction code, call get_browser_screenshot and get_page_code to inspect current UI state.\n"
            "- Use run_selenium_code for one small objective per call (single click, single fill, single submit, or single readback).\n"
            "- After each state-changing action, call get_browser_screenshot and get_page_code again to verify what changed.\n"
            "- Prioritize dismissing blockers first (cookie banners, modals, consent overlays, popups) before login/navigation actions.\n"
            "- Prefer robust selectors and fallback probing over brittle one-shot selectors.\n"
            "- Keep scripts small and deterministic; avoid long multi-step scripts and avoid hidden assumptions.\n"
            "- For credentials, never ask for secrets in chat; use list_saved_logins then get_saved_login(profile_id) inside run_selenium_code.\n"
            "- Never echo or expose credentials, raw tool scaffolding, or internal execution tags."
        )

    def get_scheduled_prompt_instructions(self) -> str:
        """Scheduled-run selenium instructions (same playbook plus concise output)."""
        return (
            f"{self.get_system_prompt_instructions()}\n"
            "- In scheduled runs, perform only the requested objective and return a concise status update."
        )

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "open_browser",
                    "description": "Open Selenium browser driver (creates driver session).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "browser": {
                                "type": "string",
                                "description": "Browser engine: chrome or firefox (default from config).",
                                "enum": ["chrome", "firefox"],
                            },
                            "headless": {
                                "type": "boolean",
                                "description": "Whether to run browser headless.",
                            },
                            "url": {
                                "type": "string",
                                "description": "Optional URL to open immediately after driver creation.",
                            },
                            "width": {
                                "type": "integer",
                                "description": "Browser window width (default 1280).",
                            },
                            "height": {
                                "type": "integer",
                                "description": "Browser window height (default 900).",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "close_browser",
                    "description": "Close and terminate Selenium browser session.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_saved_logins",
                    "description": "List available saved login profiles (IDs, labels, URL hints). Never returns raw passwords.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_selenium_code",
                    "description": (
                        "Run Python code against the live Selenium driver. "
                        "Use variable `driver`. Set `result` for return value. "
                        "For credentials, use helper `get_saved_login(profile_id)` inside code."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code snippet using `driver` and provided Selenium helpers.",
                            },
                            "timeout_seconds": {
                                "type": "integer",
                                "description": "Best-effort timeout for execution (default 20).",
                            },
                        },
                        "required": ["code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_browser_screenshot",
                    "description": (
                        "Capture current browser viewport screenshot and return detailed "
                        "visual analysis text produced by an auxiliary LLM image pass."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_page_code",
                    "description": "Return a highly compressed page digest (markdown) with key text and links.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "max_chars": {
                                "type": "integer",
                                "description": "Max output length (default from config).",
                            },
                        },
                    },
                },
            },
        ]

    async def call_tool(
        self,
        name: str,
        args: dict,
        source: str | None = None,
        channel_id: str | None = None,
        interface: object | None = None,
    ) -> str | None:
        if name == "open_browser":
            return await self._tool_open_browser(args)
        if name == "close_browser":
            return await self._tool_close_browser()
        if name == "list_saved_logins":
            return self._tool_list_saved_logins()
        if name == "run_selenium_code":
            return await self._tool_run_code(args)
        if name == "get_browser_screenshot":
            return await self._tool_screenshot(
                args,
                interface=interface,
                source=source,
                channel_id=channel_id,
            )
        if name == "get_page_code":
            return await self._tool_page_code(args)
        return None

    async def run(self, params: dict) -> TaskResult:
        """No scheduled behavior by default; this plugin is tool-driven."""
        return TaskResult(
            status="no_action",
            message=(
                "browser.selenium is a tool plugin; use open_browser, list_saved_logins, "
                "run_selenium_code, get_browser_screenshot, and get_page_code."
            ),
        )

    def _tool_list_saved_logins(self) -> str:
        if not self._login_profiles:
            return (
                "No saved login profiles configured. "
                "Run `clawquant plugin enable selenium_browser` to add them."
            )

        lines = ["Saved login profiles:"]
        for profile in self._login_profiles:
            profile_id = str(profile.get("id", "")).strip()
            if not profile_id:
                continue
            label = str(profile.get("label", "")).strip() or profile_id
            url = str(profile.get("url", "")).strip()
            username = str(profile.get("username", "")).strip()
            user_hint = self._mask_username(username)

            details = f"- {profile_id}: {label}"
            if url:
                details += f" | url={url}"
            if user_hint:
                details += f" | username={user_hint}"
            lines.append(details)

        if len(lines) == 1:
            return "No valid saved login profiles configured."
        return "\n".join(lines)

    async def _tool_open_browser(self, args: dict) -> str:
        return await asyncio.to_thread(self._open_browser_sync, args)

    def _open_browser_sync(self, args: dict) -> str:
        with self._lock:
            if self._driver is not None:
                current = ""
                try:
                    current = self._driver.current_url or ""
                except Exception:
                    pass
                return f"Browser is already open." + (f" Current URL: {current}" if current else "")

            try:
                mods = self._import_selenium_modules()
            except Exception as exc:
                return str(exc)
            webdriver = mods["webdriver"]
            chrome_options_cls = mods["ChromeOptions"]
            firefox_options_cls = mods["FirefoxOptions"]

            browser = str(args.get("browser", self._default_browser)).strip().lower() or self._default_browser
            headless = args.get("headless", self._headless)
            headless = bool(headless)
            width = int(args.get("width", 1280))
            height = int(args.get("height", 900))

            try:
                if browser == "chrome":
                    options = chrome_options_cls()
                    if headless:
                        options.add_argument("--headless=new")
                    options.add_argument("--disable-dev-shm-usage")
                    options.add_argument("--no-sandbox")
                    driver = webdriver.Chrome(options=options)
                elif browser == "firefox":
                    options = firefox_options_cls()
                    if headless:
                        options.add_argument("-headless")
                    driver = webdriver.Firefox(options=options)
                else:
                    return f"Unsupported browser '{browser}'. Use chrome or firefox."
            except Exception as exc:
                return f"Failed to open Selenium browser ({browser}): {exc}"

            self._driver = driver
            try:
                self._driver.set_window_size(width, height)
            except Exception:
                logger.debug("Unable to set window size", exc_info=True)

            url = str(args.get("url", "")).strip()
            if url:
                try:
                    self._driver.get(url)
                except Exception as exc:
                    return f"Browser opened, but failed to navigate to {url}: {exc}"

            current = ""
            try:
                current = self._driver.current_url or ""
            except Exception:
                pass

            return (
                f"Selenium browser opened ({browser}, headless={headless})."
                + (f" Current URL: {current}" if current else "")
            )

    async def _tool_close_browser(self) -> str:
        return await asyncio.to_thread(self._close_browser_sync)

    def _close_browser_sync(self) -> str:
        with self._lock:
            if self._driver is None:
                return "No active browser session."
            try:
                self._driver.quit()
            except Exception:
                logger.debug("Driver quit raised", exc_info=True)
            finally:
                self._driver = None
            return "Browser session closed."

    async def _tool_run_code(self, args: dict) -> str:
        timeout_seconds = int(args.get("timeout_seconds", 20))
        code = str(args.get("code", "")).strip()
        if not code:
            return "run_selenium_code requires non-empty `code`."
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_code_sync, code),
                timeout=max(timeout_seconds, 1),
            )
        except asyncio.TimeoutError:
            return (
                f"Code execution timed out after {timeout_seconds}s. "
                "The browser may still be busy; consider closing and reopening session."
            )

    def _run_code_sync(self, code: str) -> str:
        with self._lock:
            if self._driver is None:
                return "No active browser session. Use open_browser first."

            try:
                mods = self._import_selenium_modules()
            except Exception as exc:
                return str(exc)
            globals_map = {
                "__builtins__": {
                    "print": print,
                    "len": len,
                    "range": range,
                    "min": min,
                    "max": max,
                    "sum": sum,
                    "str": str,
                    "int": int,
                    "float": float,
                    "bool": bool,
                    "list": list,
                    "dict": dict,
                    "set": set,
                    "tuple": tuple,
                    "enumerate": enumerate,
                    "zip": zip,
                    "abs": abs,
                    "sorted": sorted,
                    "any": any,
                    "all": all,
                    "Exception": Exception,
                }
            }
            locals_map: dict[str, Any] = {
                "driver": self._driver,
                "By": mods["By"],
                "Keys": mods["Keys"],
                "WebDriverWait": mods["WebDriverWait"],
                "EC": mods["EC"],
                "result": None,
            }

            def get_saved_login(profile_id: str) -> dict[str, str]:
                profile = self._get_login_profile(profile_id)
                if profile is None:
                    raise ValueError(f"Unknown login profile '{profile_id}'")
                username = str(profile.get("username", ""))
                password = str(profile.get("password", ""))
                if not username or not password:
                    raise ValueError(f"Login profile '{profile_id}' is missing username/password")
                return {
                    "username": username,
                    "password": password,
                }

            locals_map["get_saved_login"] = get_saved_login

            stdout = io.StringIO()
            try:
                with redirect_stdout(stdout):
                    exec(compile(code, "<run_selenium_code>", "exec"), globals_map, locals_map)
            except Exception as exc:
                return f"Error executing selenium code: {exc}"

            printed = self._redact_sensitive(stdout.getvalue().strip())
            result = locals_map.get("result")
            url = ""
            title = ""
            try:
                url = self._driver.current_url or ""
            except Exception:
                pass
            try:
                title = self._driver.title or ""
            except Exception:
                pass

            lines = ["Selenium code executed."]
            if url:
                lines.append(f"URL: {url}")
            if title:
                lines.append(f"Title: {title}")
            if printed:
                lines.append(f"stdout:\n{printed}")
            if result is not None:
                lines.append(f"result: {self._redact_sensitive(str(result))}")
            return "\n".join(lines)

    async def _tool_screenshot(
        self,
        args: dict,
        interface: object | None = None,
        source: str | None = None,
        channel_id: str | None = None,
    ) -> str:
        capture = await asyncio.to_thread(self._capture_screenshot_sync)
        if isinstance(capture, str):
            return capture

        png_bytes = capture["png_bytes"]
        url = capture["url"]
        title = capture["title"]
        encoded = base64.b64encode(png_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{encoded}"

        analysis = ""
        describe = getattr(interface, "describe_image_for_tool", None)
        if callable(describe):
            try:
                maybe = describe(
                    data_url=data_url,
                    tool_name="get_browser_screenshot",
                    source=source or "unknown",
                    channel_id=channel_id or "default",
                    context={
                        "url": url,
                        "title": title,
                    },
                )
                if asyncio.iscoroutine(maybe):
                    maybe = await maybe
                analysis = str(maybe or "").strip()
            except Exception:
                logger.exception("Auxiliary screenshot analysis failed")

        if not analysis:
            analysis = (
                "Screenshot captured, but visual analysis is unavailable. "
                "Try again after confirming an LLM provider with image support is configured."
            )

        lines = ["Screenshot analysis:"]
        if url:
            lines.append(f"URL: {url}")
        if title:
            lines.append(f"Title: {title}")
        lines.append(analysis)
        return "\n".join(lines)

    def _capture_screenshot_sync(self) -> dict[str, Any] | str:
        with self._lock:
            if self._driver is None:
                return "No active browser session. Use open_browser first."

            try:
                png_bytes = self._driver.get_screenshot_as_png()
            except Exception as exc:
                return f"Failed to capture screenshot: {exc}"

            url = ""
            title = ""
            try:
                url = self._driver.current_url or ""
            except Exception:
                pass
            try:
                title = self._driver.title or ""
            except Exception:
                pass

            return {
                "png_bytes": png_bytes,
                "url": url,
                "title": title,
            }

    async def _tool_page_code(self, args: dict) -> str:
        return await asyncio.to_thread(self._page_code_sync, args)

    def _page_code_sync(self, args: dict) -> str:
        with self._lock:
            if self._driver is None:
                return "No active browser session. Use open_browser first."

            max_chars = int(args.get("max_chars", self._page_code_max_chars))
            max_chars = max(800, max_chars)

            try:
                source = self._driver.page_source or ""
                url = self._driver.current_url or ""
                title = self._driver.title or ""
            except Exception as exc:
                return f"Failed to read browser page content: {exc}"

            parser = _SlimHTMLParser()
            try:
                parser.feed(source)
            except Exception:
                logger.debug("HTML parser error", exc_info=True)

            text_items = _dedupe_text(parser.text_chunks, max_items=220)
            link_items = _dedupe_links(parser.links, max_items=120)

            lines = [
                f"# {title or 'Page'}",
                "",
                f"URL: {url}",
                "",
                "## Text",
            ]
            for chunk in text_items:
                lines.append(f"- {chunk}")

            if link_items:
                lines.extend(["", "## Links"])
                for text, href in link_items:
                    lines.append(f"- [{text}]({href})")

            output = "\n".join(lines)
            if len(output) > max_chars:
                output = output[: max_chars - 16].rstrip() + "\n...[truncated]"
            return output

    @staticmethod
    def _import_selenium_modules() -> dict[str, Any]:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.firefox.options import Options as FirefoxOptions
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except Exception as exc:
            raise RuntimeError(
                "Selenium is not available. Install plugin dependencies "
                "(e.g. `clawquant plugin enable selenium_browser`)."
            ) from exc

        return {
            "webdriver": webdriver,
            "ChromeOptions": ChromeOptions,
            "FirefoxOptions": FirefoxOptions,
            "By": By,
            "Keys": Keys,
            "WebDriverWait": WebDriverWait,
            "EC": EC,
        }

    @staticmethod
    def _mask_username(value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        if "@" in text:
            local, domain = text.split("@", 1)
            head = local[:1] if local else "*"
            return f"{head}***@{domain}"
        head = text[:1]
        tail = text[-1:] if len(text) > 1 else ""
        return f"{head}***{tail}"

    @staticmethod
    def _decode_login_profiles(blob: str) -> list[dict[str, Any]]:
        raw = (blob or "").strip()
        if not raw:
            return []

        candidates: list[Any] = []
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
            candidates.append(json.loads(decoded.decode("utf-8")))
        except Exception:
            pass

        try:
            candidates.append(json.loads(raw))
        except Exception:
            pass

        for candidate in candidates:
            if isinstance(candidate, list):
                rows = [item for item in candidate if isinstance(item, dict)]
                if rows:
                    return rows
        return []

    def _get_login_profile(self, profile_id: str) -> dict[str, Any] | None:
        lookup = str(profile_id or "").strip().lower()
        if not lookup:
            return None
        for profile in self._login_profiles:
            pid = str(profile.get("id", "")).strip().lower()
            if pid == lookup:
                return profile
        return None

    def _redact_sensitive(self, text: str) -> str:
        out = text or ""
        secrets: set[str] = set()
        for profile in self._login_profiles:
            username = str(profile.get("username", "")).strip()
            password = str(profile.get("password", "")).strip()
            if username:
                secrets.add(username)
            if password:
                secrets.add(password)
        for secret in sorted(secrets, key=len, reverse=True):
            out = out.replace(secret, "[REDACTED]")
        return out


def setup_selenium_config(
    existing_values: dict[str, Any] | None = None,
    current_values: dict[str, Any] | None = None,
    style: Any = None,
    abort_fn: Any = None,
) -> dict[str, Any]:
    """Plugin-defined setup hook for saved selenium login profiles."""
    import questionary
    from questionary import Choice

    existing_values = existing_values or {}
    current_values = current_values or {}
    current_blob = str(
        current_values.get("logins_b64")
        or existing_values.get("logins_b64")
        or ""
    ).strip()
    current_profiles = SeleniumBrowserHandler._decode_login_profiles(current_blob)
    current_count = len(current_profiles)

    def _abort() -> None:
        if callable(abort_fn):
            abort_fn()
        raise KeyboardInterrupt

    action = questionary.select(
        "Selenium saved logins:",
        choices=[
            Choice(f"Keep current ({current_count} saved)", value="keep"),
            Choice("Update saved logins", value="update"),
            Choice("Clear saved logins", value="clear"),
        ],
        default="keep" if current_count else "update",
        style=style,
    ).ask()
    if action is None:
        _abort()

    if action == "keep":
        return {}
    if action == "clear":
        return {"logins_b64": ""}

    print()
    print("  Add website login profiles for Selenium automation.")
    print("  The AI can list profile IDs and retrieve credentials during code execution.")
    print("  Credentials are stored as secret env data (not plain config values).")
    print()

    profiles: list[dict[str, str]] = []
    used_ids: set[str] = set()

    while True:
        add_more = questionary.confirm(
            "Add a login profile?",
            default=(len(profiles) == 0),
            style=style,
        ).ask()
        if add_more is None:
            _abort()
        if not add_more:
            break

        while True:
            profile_id_raw = questionary.text(
                "Profile ID (letters/numbers/-/_ only):",
                style=style,
            ).ask()
            if profile_id_raw is None:
                _abort()

            profile_id = _normalize_profile_id(profile_id_raw)
            if not profile_id:
                print("  Profile ID is required.")
                continue
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", profile_id):
                print("  Profile ID can only contain letters, numbers, '-' and '_'.")
                continue
            if profile_id in used_ids:
                print("  Profile ID already used in this setup. Choose another.")
                continue
            used_ids.add(profile_id)
            break

        label = questionary.text(
            "Label (optional):",
            default=profile_id,
            style=style,
        ).ask()
        if label is None:
            _abort()

        login_url = questionary.text(
            "Login URL (optional):",
            style=style,
        ).ask()
        if login_url is None:
            _abort()

        username = ""
        while not username:
            entered = questionary.text(
                "Email/username:",
                style=style,
            ).ask()
            if entered is None:
                _abort()
            username = entered.strip()
            if not username:
                print("  Email/username is required.")

        password = ""
        while not password:
            entered = questionary.password(
                "Password:",
                style=style,
            ).ask()
            if entered is None:
                _abort()
            password = entered
            if not password:
                print("  Password is required.")

        profiles.append({
            "id": profile_id,
            "label": (label or profile_id).strip(),
            "url": (login_url or "").strip(),
            "username": username,
            "password": password,
        })

    if not profiles:
        print("  No login profiles saved.")
        return {"logins_b64": ""}

    encoded = _encode_login_profiles(profiles)
    print(f"  Saved {len(profiles)} login profile(s).")
    return {"logins_b64": encoded}


def _normalize_profile_id(value: str) -> str:
    return value.strip().replace(" ", "_")


def _encode_login_profiles(profiles: list[dict[str, str]]) -> str:
    payload = json.dumps(profiles, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")

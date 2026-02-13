"""Lightweight aiohttp server -- the core HTTP API.

Exposes routes for plugins and the AI interface to interact with the system.
~200 lines. No framework magic, no middleware stack.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from core.bus import AsyncIOBus
    from core.config import AppConfig
    from core.data.store import Store
    from core.registry import PluginRegistry
    from scheduler.runner import Scheduler

logger = logging.getLogger(__name__)


def create_app(
    config: AppConfig,
    bus: AsyncIOBus,
    store: Store,
    registry: PluginRegistry,
    scheduler: Scheduler,
) -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()

    # Store references for route handlers
    app["config"] = config
    app["bus"] = bus
    app["store"] = store
    app["registry"] = registry
    app["scheduler"] = scheduler

    # Register routes
    app.router.add_get("/health", handle_health)
    app.router.add_post("/events", handle_publish_event)
    app.router.add_get("/events", handle_stream_events)
    app.router.add_get("/state/portfolio", handle_get_portfolio)
    app.router.add_get("/state/portfolio/{portfolio_type}", handle_get_portfolio)
    app.router.add_get("/state/tasks", handle_get_tasks)
    app.router.add_post("/tasks", handle_create_task)
    app.router.add_delete("/tasks/{task_id}", handle_delete_task)
    app.router.add_get("/state/signals", handle_get_signals)
    app.router.add_get("/state/memories", handle_get_memories)
    app.router.add_get("/state/plugins", handle_get_plugins)

    return app


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_health(request: web.Request) -> web.Response:
    """GET /health -- health check."""
    from core.models import Event  # local import to avoid circular

    registry: PluginRegistry = request.app["registry"]
    return web.json_response({
        "status": "ok",
        "plugins": registry.summary(),
    })


async def handle_publish_event(request: web.Request) -> web.Response:
    """POST /events -- publish an event (used by plugins to push data).

    Body: {"type": "integration.input", "source": "telegram", "payload": {...}}
    """
    from core.models.events import Event

    bus: AsyncIOBus = request.app["bus"]

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if "type" not in body or "source" not in body:
        return web.json_response(
            {"error": "Missing required fields: type, source"},
            status=400,
        )

    event = Event(
        type=body["type"],
        source=body["source"],
        payload=body.get("payload", {}),
        correlation_id=body.get("correlation_id", ""),
        metadata=body.get("metadata"),
    )

    await bus.publish(event)

    return web.json_response({
        "id": event.id,
        "correlation_id": event.correlation_id,
        "type": event.type,
    }, status=201)


async def handle_stream_events(request: web.Request) -> web.StreamResponse:
    """GET /events -- Server-Sent Events stream for real-time updates.

    Subscribes to all events on the bus and streams them to the client.
    """
    from core.models.events import Event
    import asyncio

    bus: AsyncIOBus = request.app["bus"]

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    queue: asyncio.Queue[Event] = asyncio.Queue()

    async def forward_event(event: Event) -> None:
        await queue.put(event)

    bus.subscribe("*", forward_event)

    try:
        while True:
            event = await queue.get()
            data = event.model_dump_json()
            await response.write(f"event: {event.type}\ndata: {data}\n\n".encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        bus.unsubscribe("*", forward_event)

    return response


async def handle_get_portfolio(request: web.Request) -> web.Response:
    """GET /state/portfolio[/{ai|human}] -- get portfolio positions."""
    from core.models.signals import Position

    store: Store = request.app["store"]
    portfolio_type = request.match_info.get("portfolio_type")

    result = {}

    if portfolio_type in (None, "ai"):
        ai_positions = store.list_json("positions/ai", Position)
        result["ai"] = [p.model_dump(mode="json") for p in ai_positions]

    if portfolio_type in (None, "human"):
        human_positions = store.list_json("positions/human", Position)
        result["human"] = [p.model_dump(mode="json") for p in human_positions]

    if portfolio_type and portfolio_type not in ("ai", "human"):
        return web.json_response(
            {"error": "portfolio_type must be 'ai' or 'human'"},
            status=400,
        )

    return web.json_response(result)


async def handle_get_tasks(request: web.Request) -> web.Response:
    """GET /state/tasks -- list all scheduled tasks."""
    scheduler: Scheduler = request.app["scheduler"]
    tasks = scheduler.list_tasks()
    return web.json_response([t.model_dump(mode="json") for t in tasks])


async def handle_create_task(request: web.Request) -> web.Response:
    """POST /tasks -- create a new scheduled task.

    Body: {"name": "...", "type": "recurring", "cron_expression": "...", "handler": "..."}
    """
    from core.models.tasks import Task

    scheduler: Scheduler = request.app["scheduler"]

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if "name" not in body or "handler" not in body:
        return web.json_response(
            {"error": "Missing required fields: name, handler"},
            status=400,
        )

    task = Task(**body)
    await scheduler.create_task(task)

    return web.json_response(task.model_dump(mode="json"), status=201)


async def handle_delete_task(request: web.Request) -> web.Response:
    """DELETE /tasks/{task_id} -- delete a scheduled task."""
    scheduler: Scheduler = request.app["scheduler"]
    task_id = request.match_info["task_id"]

    deleted = await scheduler.delete_task(task_id)
    if deleted:
        return web.json_response({"deleted": task_id})
    return web.json_response({"error": "Task not found"}, status=404)


async def handle_get_signals(request: web.Request) -> web.Response:
    """GET /state/signals -- list all signals."""
    from core.models.signals import Signal

    store: Store = request.app["store"]
    signals = store.list_json("signals", Signal)
    return web.json_response([s.model_dump(mode="json") for s in signals])


async def handle_get_memories(request: web.Request) -> web.Response:
    """GET /state/memories -- list memories, optionally filtered."""
    from core.models.memories import Memory

    store: Store = request.app["store"]

    # Optional query params
    ticker = request.query.get("ticker")
    tag = request.query.get("tag")
    limit = int(request.query.get("limit", "20"))

    tags = [tag] if tag else None

    if ticker or tags:
        # Use SQLite index for filtered queries
        memory_ids = store.search_memories(ticker=ticker, tags=tags, limit=limit)
        memories = []
        for mid in memory_ids:
            mem = store.read_json("memories", f"{mid}.json", Memory)
            if mem:
                memories.append(mem)
    else:
        memories = store.list_json("memories", Memory)[:limit]

    return web.json_response([m.model_dump(mode="json") for m in memories])


async def handle_get_plugins(request: web.Request) -> web.Response:
    """GET /state/plugins -- list all registered plugins."""
    registry: PluginRegistry = request.app["registry"]
    return web.json_response(registry.summary())

"""Memory retrieval -- finds relevant memories for context packs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.data.store import Store
from core.models.memories import Memory

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """Retrieves relevant memories from the store for inclusion in context packs.

    Filters by ticker, tags, and recency. Ranked by relevance.
    """

    def __init__(
        self,
        store: Store,
        max_memories: int = 10,
        relevance_window_days: int = 90,
    ) -> None:
        self._store = store
        self._max_memories = max_memories
        self._relevance_window_days = relevance_window_days

    def retrieve(
        self,
        ticker: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        """Retrieve relevant memories, filtered and ranked.

        Args:
            ticker: Filter by ticker (e.g., "NVDA")
            tags: Filter by tags (e.g., ["earnings", "tech"])
            limit: Max memories to return (defaults to configured max)
        """
        max_count = limit or self._max_memories
        since = datetime.now(timezone.utc) - timedelta(days=self._relevance_window_days)

        # Query the SQLite index for matching memory IDs
        memory_ids = self._store.search_memories(
            ticker=ticker,
            tags=tags,
            since=since,
            limit=max_count,
        )

        # Load full memory objects from JSON files
        memories: list[Memory] = []
        for mid in memory_ids:
            mem = self._store.read_json("memories", f"{mid}.json", Memory)
            if mem:
                memories.append(mem)

        return memories

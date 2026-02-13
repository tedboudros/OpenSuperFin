"""File + SQLite storage layer.

Files (JSON, Markdown, JSONL) are the source of truth for human-readable state.
SQLite provides indexed queries for market data and memory retrieval.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from core.models.market import MarketData
from core.models.memories import Memory

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class Store:
    """Unified storage layer for files + SQLite.

    All paths are relative to the home directory (~/.opensuperfin/).
    """

    def __init__(self, home: Path) -> None:
        self._home = home
        self._db_path = home / "db.sqlite"
        self._db: sqlite3.Connection | None = None
        self._init_sqlite()

    # ------------------------------------------------------------------
    # SQLite
    # ------------------------------------------------------------------

    def _init_sqlite(self) -> None:
        """Initialize SQLite database and create tables if needed."""
        self._db = sqlite3.connect(str(self._db_path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")

        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS market_data (
                ticker TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                available_at TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL NOT NULL,
                volume REAL,
                source TEXT DEFAULT '',
                data_type TEXT DEFAULT 'price',
                metadata TEXT,
                PRIMARY KEY (ticker, timestamp, source)
            );

            CREATE INDEX IF NOT EXISTS idx_market_available
                ON market_data(ticker, available_at);

            CREATE TABLE IF NOT EXISTS memory_index (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                who_was_right TEXT,
                tags TEXT DEFAULT '',
                ticker TEXT DEFAULT '',
                confidence_impact REAL DEFAULT 0.0,
                source TEXT DEFAULT 'production'
            );

            CREATE INDEX IF NOT EXISTS idx_memory_ticker
                ON memory_index(ticker);
            CREATE INDEX IF NOT EXISTS idx_memory_created
                ON memory_index(created_at);

            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_channel_created
                ON conversation_messages(channel_id, created_at, id);
        """)
        self._db.commit()
        logger.info("SQLite initialized at %s", self._db_path)

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("Store not initialized")
        return self._db

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._db:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Market data (SQLite)
    # ------------------------------------------------------------------

    def save_market_data(self, data: list[MarketData]) -> int:
        """Insert market data rows into SQLite. Returns count of rows inserted."""
        if not data:
            return 0

        inserted = 0
        for d in data:
            try:
                self.db.execute(
                    """INSERT OR REPLACE INTO market_data
                       (ticker, timestamp, available_at, open, high, low, close,
                        volume, source, data_type, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        d.ticker,
                        d.timestamp.isoformat(),
                        d.available_at.isoformat(),
                        d.open,
                        d.high,
                        d.low,
                        d.close,
                        d.volume,
                        d.source,
                        d.data_type,
                        json.dumps(d.metadata) if d.metadata else None,
                    ),
                )
                inserted += 1
            except sqlite3.Error:
                logger.exception("Failed to insert market data for %s", d.ticker)

        self.db.commit()
        return inserted

    def query_market_data(
        self,
        ticker: str,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[MarketData]:
        """Query market data for a ticker, optionally filtered by TimeContext."""
        if as_of:
            rows = self.db.execute(
                """SELECT * FROM market_data
                   WHERE ticker = ? AND available_at <= ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (ticker, as_of.isoformat(), limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT * FROM market_data
                   WHERE ticker = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (ticker, limit),
            ).fetchall()

        return [self._row_to_market_data(r) for r in rows]

    def get_latest_price(self, ticker: str, as_of: datetime | None = None) -> float | None:
        """Get the most recent close price for a ticker."""
        data = self.query_market_data(ticker, as_of=as_of, limit=1)
        return data[0].close if data else None

    def _row_to_market_data(self, row: sqlite3.Row) -> MarketData:
        return MarketData(
            ticker=row["ticker"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            available_at=datetime.fromisoformat(row["available_at"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            source=row["source"] or "",
            data_type=row["data_type"] or "price",
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    # ------------------------------------------------------------------
    # Memory index (SQLite + JSON files)
    # ------------------------------------------------------------------

    def index_memory(self, memory: Memory) -> None:
        """Add or update a memory in the SQLite index."""
        # Extract ticker from tags if present (first tag that looks like a ticker)
        ticker = ""
        for tag in memory.tags:
            if tag.isupper() or tag.startswith("$"):
                ticker = tag.lstrip("$")
                break

        self.db.execute(
            """INSERT OR REPLACE INTO memory_index
               (id, created_at, who_was_right, tags, ticker, confidence_impact, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.id,
                memory.created_at.isoformat(),
                memory.who_was_right,
                ",".join(memory.tags),
                ticker,
                memory.confidence_impact,
                memory.source,
            ),
        )
        self.db.commit()

    def search_memories(
        self,
        ticker: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[str]:
        """Search the memory index and return matching memory IDs.

        Results can be loaded from JSON files using read_json().
        """
        conditions = []
        params: list = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)

        if tags:
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f"%{tag}%")
            conditions.append(f"({' OR '.join(tag_conditions)})")

        if since:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = self.db.execute(
            f"SELECT id FROM memory_index {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        return [row["id"] for row in rows]

    # ------------------------------------------------------------------
    # Conversation history (SQLite)
    # ------------------------------------------------------------------

    def append_conversation_message(self, channel_id: str, role: str, content: str) -> None:
        """Append one chat message to persistent conversation history."""
        self.db.execute(
            """INSERT INTO conversation_messages (channel_id, role, content, created_at)
               VALUES (?, ?, ?, ?)""",
            (channel_id, role, content, datetime.now().isoformat()),
        )
        self.db.commit()

    def load_conversation_history(self) -> dict[str, list[dict]]:
        """Load full persisted conversation history for all channels."""
        rows = self.db.execute(
            """SELECT channel_id, role, content
               FROM conversation_messages
               ORDER BY channel_id ASC, created_at ASC, id ASC"""
        ).fetchall()

        history: dict[str, list[dict]] = {}
        for row in rows:
            channel = row["channel_id"]
            history.setdefault(channel, []).append({
                "role": row["role"],
                "content": row["content"],
            })
        return history

    # ------------------------------------------------------------------
    # File operations (JSON, Markdown, JSONL)
    # ------------------------------------------------------------------

    def write_json(self, subdir: str, filename: str, model: BaseModel) -> Path:
        """Write a Pydantic model as a JSON file."""
        path = self._home / subdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2))
        return path

    def read_json(self, subdir: str, filename: str, model_class: type[T]) -> T | None:
        """Read a JSON file and parse it as a Pydantic model."""
        path = self._home / subdir / filename
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return model_class(**data)
        except (json.JSONDecodeError, Exception):
            logger.exception("Failed to read %s", path)
            return None

    def list_json(self, subdir: str, model_class: type[T]) -> list[T]:
        """List and parse all JSON files in a subdirectory."""
        dirpath = self._home / subdir
        if not dirpath.exists():
            return []

        results = []
        for filepath in sorted(dirpath.glob("*.json")):
            try:
                data = json.loads(filepath.read_text())
                results.append(model_class(**data))
            except (json.JSONDecodeError, Exception):
                logger.exception("Failed to parse %s", filepath)
        return results

    def delete_file(self, subdir: str, filename: str) -> bool:
        """Delete a file. Returns True if it existed."""
        path = self._home / subdir / filename
        if path.exists():
            path.unlink()
            return True
        return False

    def write_markdown(self, subdir: str, filename: str, content: str) -> Path:
        """Write a Markdown file."""
        path = self._home / subdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def file_exists(self, subdir: str, filename: str) -> bool:
        """Check if a file exists."""
        return (self._home / subdir / filename).exists()

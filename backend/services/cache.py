"""Small SQLite cache. Short transactions run off the asyncio event loop."""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from backend.models import Article, Neighbors
from backend.services.parser import PARSER_VERSION


class SQLiteCache:
    def __init__(self, path: Path, ttl_seconds: int, parser_version: int = PARSER_VERSION):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.parser_version = parser_version
        self.lock = threading.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS articles (alias TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS neighbors (page_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL, parser_version INTEGER NOT NULL)")

    async def get_articles(self, titles: list[str]) -> dict[str, Article]:
        return await asyncio.to_thread(self._get_articles, titles)

    def _get_articles(self, titles: list[str]) -> dict[str, Article]:
        result = {}
        with self.lock, sqlite3.connect(self.path) as connection:
            for title in titles:
                row = connection.execute("SELECT payload FROM articles WHERE alias = ? AND fetched_at > ?", (title, time.time() - self.ttl_seconds)).fetchone()
                if row:
                    result[title] = Article.model_validate_json(row[0])
        return result

    async def save_articles(self, articles: dict[str, Article]) -> None:
        await asyncio.to_thread(self._save_articles, articles)

    def _save_articles(self, articles: dict[str, Article]) -> None:
        rows = [(alias, article.model_dump_json(), time.time()) for alias, article in articles.items()]
        with self.lock, sqlite3.connect(self.path) as connection:
            connection.executemany("INSERT OR REPLACE INTO articles VALUES (?, ?, ?)", rows)

    async def get_neighbors(self, page_id: int) -> Neighbors | None:
        return await asyncio.to_thread(self._get_neighbors, page_id)

    def _get_neighbors(self, page_id: int) -> Neighbors | None:
        with self.lock, sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT payload FROM neighbors WHERE page_id = ? AND fetched_at > ? AND parser_version = ?", (page_id, time.time() - self.ttl_seconds, self.parser_version)).fetchone()
        return Neighbors.model_validate_json(row[0]) if row else None

    async def save_neighbors(self, neighbors: Neighbors) -> None:
        await asyncio.to_thread(self._save_neighbors, neighbors)

    def _save_neighbors(self, neighbors: Neighbors) -> None:
        row = (neighbors.article.page_id, neighbors.model_dump_json(), neighbors.fetched_at.timestamp(), self.parser_version)
        with self.lock, sqlite3.connect(self.path) as connection:
            connection.execute("INSERT OR REPLACE INTO neighbors VALUES (?, ?, ?, ?)", row)

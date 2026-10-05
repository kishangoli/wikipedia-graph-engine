import sqlite3
from datetime import datetime, timezone

import pytest

from backend.models import Article, Neighbors
from backend.services.cache import SQLiteCache


@pytest.fixture
async def cache(tmp_path):
    cache = SQLiteCache(tmp_path / "cache.sqlite3", 86_400)
    await cache.initialize()
    return cache


async def test_aliases_and_empty_neighbor_lists_survive_restart(cache):
    article = Article(page_id=1, title="Cat")
    neighbors = Neighbors(article=article, links=[], fetched_at=datetime.now(timezone.utc), revision_id=12)
    await cache.save_articles({"Domestic cat": article, "Cat": article})
    await cache.save_neighbors(neighbors)
    reopened = SQLiteCache(cache.path, cache.ttl_seconds)
    assert (await reopened.get_articles(["Domestic cat"]))["Domestic cat"] == article
    assert await reopened.get_neighbors(1) == neighbors


async def test_stale_and_incompatible_entries_are_misses(cache):
    article = Article(page_id=1, title="Cat")
    await cache.save_articles({"Cat": article})
    await cache.save_neighbors(Neighbors(article=article, links=[], fetched_at=datetime.now(timezone.utc)))
    incompatible = SQLiteCache(cache.path, cache.ttl_seconds, parser_version=999)
    assert await incompatible.get_neighbors(1) is None
    with sqlite3.connect(cache.path) as connection:
        connection.execute("UPDATE articles SET fetched_at = 0")
        connection.execute("UPDATE neighbors SET fetched_at = 0")
    assert await cache.get_articles(["Cat"]) == {}
    assert await cache.get_neighbors(1) is None

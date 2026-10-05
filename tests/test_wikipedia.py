import asyncio

import httpx
import pytest

from backend.config import Settings
from backend.models import Article, SearchStats
from backend.services.cache import SQLiteCache
from backend.services.errors import InvalidArticle, SearchLimit, WikipediaError
from backend.services.wikipedia import WikipediaClient


@pytest.fixture
async def cache(tmp_path):
    cache = SQLiteCache(tmp_path / "cache.sqlite3", 86_400)
    await cache.initialize()
    return cache


def settings_for(cache, **overrides):
    return Settings(cache_path=cache.path, request_interval_seconds=0, **overrides)


async def test_redirects_fragments_deduplication_and_cache(cache):
    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.params["action"] == "parse":
            html = '<div class="mw-parser-output"><p><a href="/wiki/Kitten#Care">young cat</a><a href="/wiki/Cat">cat</a><a href="/wiki/Alias">self</a><a href="/wiki/Gone">gone</a></p></div>'
            return httpx.Response(200, json={"parse": {"pageid": 1, "title": "Start", "revid": 42, "text": html}})
        return httpx.Response(200, json={"query": {"redirects": [{"from": "Kitten", "to": "Cat"}, {"from": "Alias", "to": "Start"}], "pages": [{"pageid": 1, "title": "Start", "ns": 0}, {"pageid": 2, "title": "Cat", "ns": 0}, {"title": "Gone", "ns": 0, "missing": True}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WikipediaClient(settings_for(cache), cache, http)
        stats = SearchStats()
        result = await client.neighbors(Article(page_id=1, title="Start"), stats)
        assert len(result.links) == 1
        assert result.links[0].destination == Article(page_id=2, title="Cat")
        assert result.links[0].anchor_text == "young cat"
        assert result.links[0].url.endswith("Kitten#Care")
        assert result.revision_id == 42
        assert await client.neighbors(result.article, stats) == result
        assert stats.cache_hits == 1
        assert stats.network_requests == 2
        assert len(calls) == 2


async def test_missing_pages_are_invalid_inputs(cache):
    def handler(request):
        return httpx.Response(200, json={"query": {"pages": [{"title": "Missing", "ns": 0, "missing": True}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WikipediaClient(settings_for(cache), cache, http)
        with pytest.raises(InvalidArticle):
            await client.resolve_inputs("Missing", "Missing", SearchStats())


async def test_title_normalization_and_redirect_resolution(cache):
    def handler(request):
        return httpx.Response(200, json={"query": {"normalized": [{"from": "kitty", "to": "Kitty"}], "redirects": [{"from": "Kitty", "to": "Cat"}], "pages": [{"pageid": 2, "title": "Cat", "ns": 0}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WikipediaClient(settings_for(cache), cache, http)
        start, end = await client.resolve_inputs("kitty", "Cat", SearchStats())
        assert start.page_id == end.page_id == 2
        assert (await cache.get_articles(["kitty"]))["kitty"].title == "Cat"


async def test_simultaneous_expansions_share_fetch(cache):
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"parse": {"pageid": 1, "text": '<div class="mw-parser-output"><p>No links</p></div>'}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WikipediaClient(settings_for(cache), cache, http)
        article = Article(page_id=1, title="Start")
        results = await asyncio.gather(client.neighbors(article, SearchStats()), client.neighbors(article, SearchStats()))
        assert results[0] == results[1]
        assert len(calls) == 1
        assert client.inflight == {}


@pytest.mark.parametrize("response", [{"parse": {"pageid": 1, "text": "broken HTML"}}, {"parse": {"pageid": 1}}, {"query": {}}, ["unexpected"]])
async def test_failed_parsing_does_not_cache_empty_neighbors(cache, response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as http:
        client = WikipediaClient(settings_for(cache), cache, http)
        with pytest.raises(WikipediaError):
            await client.neighbors(Article(page_id=1, title="Start"), SearchStats())
    assert await cache.get_neighbors(1) is None


async def test_throttled_requests_retry_and_count_attempts(cache):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"query": {"pages": [{"pageid": 1, "title": "Start", "ns": 0}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WikipediaClient(settings_for(cache), cache, http)
        stats = SearchStats()
        assert (await client.resolve_many(["Start"], stats))["Start"].page_id == 1
        assert stats.network_requests == 2


async def test_uncached_budget_does_not_block_cache_hits(cache):
    from datetime import datetime, timezone
    from backend.models import Neighbors

    article = Article(page_id=1, title="Start")
    await cache.save_neighbors(Neighbors(article=article, links=[], fetched_at=datetime.now(timezone.utc)))
    async with httpx.AsyncClient() as http:
        client = WikipediaClient(settings_for(cache, max_uncached_expansions=1), cache, http)
        stats = SearchStats(uncached_expansions=1)
        assert (await client.neighbors(article, stats)).links == []
        with pytest.raises(SearchLimit):
            await client.neighbors(Article(page_id=2, title="Other"), stats)

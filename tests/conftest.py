import asyncio
from datetime import datetime, timezone

import pytest

from backend.config import Settings
from backend.models import Article, ArticleLink, Neighbors
from backend.services.errors import InvalidArticle, SearchLimit, WikipediaError
from backend.services.parser import article_url, parse_input
from backend.services.search import SearchManager


class FakeWikipedia:
    """Controlled directed graph for deterministic search and API verification."""

    def __init__(self, graph, settings, delays=None, failures=None):
        titles = list(dict.fromkeys([*graph, *(title for links in graph.values() for title in links)]))
        self.articles = {title: Article(page_id=index + 1, title=title) for index, title in enumerate(titles)}
        self.graph = graph
        self.settings = settings
        self.delays = delays or {}
        self.failures = failures or set()
        self.expansions = []
        self.active_fetches = 0

    async def resolve_inputs(self, start, destination, stats):
        try:
            return self.articles[parse_input(start)], self.articles[parse_input(destination)]
        except KeyError as error:
            raise InvalidArticle("Article does not exist.") from error

    async def neighbors(self, article, stats, progress=None):
        self.expansions.append(article.title)
        if stats.uncached_expansions >= self.settings.max_uncached_expansions:
            raise SearchLimit("Uncached article expansion limit reached.")
        stats.uncached_expansions += 1
        self.active_fetches += 1
        try:
            if progress:
                await progress({"stage": "fetching", "message": "Downloading article HTML."})
            await asyncio.sleep(self.delays.get(article.title, 0))
            if article.title in self.failures:
                raise WikipediaError("Fetch failed.")
            links = [ArticleLink(source=article, destination=self.articles[title], anchor_text=f"link to {title}", url=article_url(title)) for title in self.graph.get(article.title, [])]
            return Neighbors(article=article, links=links, fetched_at=datetime.now(timezone.utc))
        finally:
            self.active_fetches -= 1


@pytest.fixture
def make_manager(tmp_path):
    def factory(graph, delays=None, failures=None, **overrides):
        settings = Settings(cache_path=tmp_path / "cache.sqlite3", request_interval_seconds=0, **overrides)
        wikipedia = FakeWikipedia(graph, settings, delays, failures)
        return SearchManager(settings, wikipedia)
    return factory

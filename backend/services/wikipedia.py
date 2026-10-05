"""Fetch English Wikipedia articles and resolve every accepted link to a page identity."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from backend.config import Settings
from backend.models import Article, ArticleLink, Neighbors, SearchStats
from backend.services.cache import SQLiteCache
from backend.services.errors import InvalidArticle, SearchLimit, WikipediaError
from backend.services.parser import extract_links, parse_input


ProgressCallback = Callable[[dict], Awaitable[None]]


async def notify(progress: ProgressCallback | None, update: dict) -> None:
    if progress is not None:
        await progress(update)


class WikipediaClient:
    def __init__(self, settings: Settings, cache: SQLiteCache, http: httpx.AsyncClient):
        self.settings = settings
        self.cache = cache
        self.http = http
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self.pacing_lock = asyncio.Lock()
        self.resolve_lock = asyncio.Lock()
        self.next_request_at = 0.0
        self.inflight: dict[int, asyncio.Task] = {}

    async def _pace(self) -> None:
        async with self.pacing_lock:
            await asyncio.sleep(max(0, self.next_request_at - time.monotonic()))
            self.next_request_at = time.monotonic() + self.settings.request_interval_seconds

    async def _backoff(self, delay: float) -> None:
        async with self.pacing_lock:
            self.next_request_at = max(self.next_request_at, time.monotonic() + delay)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(0, float(header))
            except ValueError:
                try:
                    return max(0, parsedate_to_datetime(header).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
        return 2 ** attempt

    async def _request(self, params: dict, stats: SearchStats, progress: ProgressCallback | None = None) -> dict:
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        for attempt in range(self.settings.request_retries + 1):
            try:
                await notify(progress, {"stage": "waiting", "message": "Waiting for a Wikipedia request slot."})
                async with self.semaphore:
                    await self._pace()
                    stats.network_requests += 1
                    message = "Downloading article HTML." if params["action"] == "parse" else "Checking linked article identities and redirects."
                    await notify(progress, {"stage": "fetching" if params["action"] == "parse" else "validating", "message": message, "wait_seconds": None})
                    response = await self.http.get(self.settings.wikipedia_api_url, params=params)
                if response.status_code in {429, 500, 502, 503, 504}:
                    if attempt < self.settings.request_retries:
                        delay = self._retry_delay(response, attempt)
                        await notify(progress, {"stage": "retrying", "message": f"Wikipedia returned HTTP {response.status_code}; retrying after {delay:g}s.", "wait_seconds": delay})
                        await self._backoff(delay)
                        continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise WikipediaError("Wikipedia returned an unexpected response.")
                if "error" in data:
                    error = data["error"]
                    if not isinstance(error, dict):
                        raise WikipediaError("Wikipedia returned a malformed API error.")
                    if error.get("code") in {"maxlag", "ratelimited", "readonly"} and attempt < self.settings.request_retries:
                        delay = self._retry_delay(response, attempt)
                        await notify(progress, {"stage": "retrying", "message": f"Wikipedia reported {error['code']}; retrying after {delay:g}s.", "wait_seconds": delay})
                        await self._backoff(delay)
                        continue
                    raise WikipediaError(f"Wikipedia API error: {error.get('code', 'unknown')}.")
                return data
            except httpx.TransportError as error:
                if attempt >= self.settings.request_retries:
                    raise WikipediaError("Could not reach Wikipedia after retries.") from error
                await notify(progress, {"stage": "retrying", "message": f"Network request failed; retrying after {2 ** attempt}s.", "wait_seconds": 2 ** attempt})
                await self._backoff(2 ** attempt)
            except httpx.HTTPStatusError as error:
                raise WikipediaError(f"Wikipedia returned HTTP {error.response.status_code}.") from error
            except ValueError as error:
                raise WikipediaError("Wikipedia returned invalid JSON.") from error
        raise WikipediaError("Wikipedia request failed.")

    async def resolve_many(self, titles: list[str], stats: SearchStats, progress: ProgressCallback | None = None) -> dict[str, Article | None]:
        titles = list(dict.fromkeys(titles))
        if self.resolve_lock.locked():
            await notify(progress, {"stage": "waiting", "message": "Waiting for another article's identity lookup to finish."})
        async with self.resolve_lock:
            cached = await self.cache.get_articles(titles)
            stats.metadata_cache_hits += len(cached)
            result: dict[str, Article | None] = dict(cached)
            missing = [title for title in titles if title not in cached]
            for offset in range(0, len(missing), 50):
                batch = missing[offset:offset + 50]
                data = await self._request({"action": "query", "titles": "|".join(batch), "redirects": "1", "prop": "info"}, stats, progress)
                try:
                    query = data["query"]
                    pages = query["pages"]
                    if not isinstance(pages, list) or not pages:
                        raise ValueError("Missing page records")
                    by_title = {page["title"]: page for page in pages}
                    aliases = {entry["from"]: entry["to"] for entry in query.get("normalized", []) + query.get("redirects", [])}
                    resolved = {}
                    for title in batch:
                        target = title
                        visited = set()
                        while target in aliases and target not in visited:
                            visited.add(target)
                            target = aliases[target]
                        if target in visited:
                            result[title] = None
                            continue
                        page = by_title[target]
                        if "missing" in page or "invalid" in page or page["ns"] != 0 or "redirect" in page:
                            result[title] = None
                            continue
                        article = Article(page_id=page["pageid"], title=page["title"])
                        result[title] = article
                        resolved[title] = article
                        resolved[article.title] = article
                    await self.cache.save_articles(resolved)
                except (KeyError, TypeError, ValueError) as error:
                    raise WikipediaError("Wikipedia returned incomplete article metadata.") from error
            return result

    async def resolve_inputs(self, start: str, destination: str, stats: SearchStats) -> tuple[Article, Article]:
        titles = [parse_input(start), parse_input(destination)]
        resolved = await self.resolve_many(titles, stats)
        for title in titles:
            if resolved[title] is None:
                raise InvalidArticle(f"'{title}' is not an existing English Wikipedia article.")
        return resolved[titles[0]], resolved[titles[1]]

    async def neighbors(self, article: Article, stats: SearchStats, progress: ProgressCallback | None = None) -> Neighbors:
        # Concurrent callers share one expansion and one cache write.
        task = self.inflight.get(article.page_id)
        if task is None:
            task = asyncio.create_task(self._neighbors(article, stats, progress))
            self.inflight[article.page_id] = task
        try:
            return await task
        finally:
            if task.done() and self.inflight.get(article.page_id) is task:
                del self.inflight[article.page_id]

    async def _neighbors(self, article: Article, stats: SearchStats, progress: ProgressCallback | None = None) -> Neighbors:
        await notify(progress, {"stage": "cache", "message": "Checking the local article cache."})
        cached = await self.cache.get_neighbors(article.page_id)
        if cached is not None:
            stats.cache_hits += 1
            await notify(progress, {"stage": "expanded", "message": "Loaded complete links from the local cache.", "checked": len(cached.links), "total": len(cached.links), "accepted": len(cached.links), "links": cached.links, "fetched_at": cached.fetched_at, "revision_id": cached.revision_id})
            return cached
        if stats.uncached_expansions >= self.settings.max_uncached_expansions:
            raise SearchLimit("Uncached article expansion limit reached.")
        stats.uncached_expansions += 1
        data = await self._request({"action": "parse", "pageid": str(article.page_id), "prop": "text|revid", "disableeditsection": "1"}, stats, progress)
        try:
            parsed = data["parse"]
            html = parsed["text"]
            if not isinstance(html, str) or parsed["pageid"] != article.page_id:
                raise ValueError("Unexpected parsed article")
            fetched_at = datetime.now(timezone.utc)
            await notify(progress, {"stage": "parsing", "message": "Article downloaded; extracting linked words from its text."})
            raw_links = await asyncio.to_thread(extract_links, html, article.title)
            total = len(raw_links)
            await notify(progress, {"stage": "validating", "message": f"Found {total} candidate links; checking their destinations.", "total": total})
            links = []
            seen = {article.page_id}
            # Stream complete, canonical edges in small batches, without caching partial lists.
            for offset in range(0, total, 50):
                batch = raw_links[offset:offset + 50]
                targets = await self.resolve_many([link.title for link in batch], stats, progress)
                accepted = []
                for link in batch:
                    target = targets[link.title]
                    if target is not None and target.page_id not in seen:
                        seen.add(target.page_id)
                        accepted.append(ArticleLink(source=article, destination=target, anchor_text=link.anchor_text, url=link.url))
                links.extend(accepted)
                checked = min(offset + len(batch), total)
                await notify(progress, {"stage": "validating", "message": f"Checked {checked} of {total} linked destinations; {len(links)} valid connections.", "checked": checked, "total": total, "accepted": len(links), "links": accepted, "fetched_at": fetched_at, "revision_id": parsed.get("revid")})
            neighbors = Neighbors(article=article, links=links, fetched_at=fetched_at, revision_id=parsed.get("revid"))
        except (KeyError, TypeError, ValueError) as error:
            raise WikipediaError("Wikipedia returned malformed article content.") from error
        await self.cache.save_neighbors(neighbors)
        await notify(progress, {"stage": "expanded", "message": f"Finished article; cached {len(links)} qualifying connections.", "checked": total, "total": total, "accepted": len(links), "fetched_at": fetched_at, "revision_id": neighbors.revision_id})
        return neighbors

"""Layer-ordered BFS and an in-memory lifecycle for one active local search."""

import asyncio
import logging
import time
from collections import deque
from uuid import uuid4

from backend.config import Settings
from backend.models import Article, ArticleLink, GraphNode, GraphSample, SearchActivity, SearchRequest, SearchSnapshot, SearchStats
from backend.services.errors import SearchBusy, SearchLimit, WikipediaError
from backend.services.wikipedia import WikipediaClient


logger = logging.getLogger(__name__)


class PathFound(Exception):
    """Stop pending expansions once a shortest route is established in the current layer."""


class SearchJob:
    def __init__(self, start: Article, destination: Article, stats: SearchStats, settings: Settings, started_at: float):
        self.id = uuid4().hex
        self.start = start
        self.destination = destination
        self.stats = stats
        self.settings = settings
        self.started_at = started_at
        self.status = "running"
        self.message = "Exploring article hyperlinks."
        self.route: list[ArticleLink] = []
        self.graph = GraphSample(nodes=[GraphNode(article=start, depth=0)])
        self.data_fetched_at = {}
        self.activities: dict[int, SearchActivity] = {}
        self.activity_log = deque(maxlen=20)
        self.events = deque(maxlen=settings.event_history_limit)
        self.sequence = 0
        self.condition = asyncio.Condition()
        self.task: asyncio.Task | None = None

    @property
    def terminal(self) -> bool:
        return self.status != "running"

    def snapshot(self) -> SearchSnapshot:
        if not self.terminal:
            self.stats.elapsed_seconds = round(time.monotonic() - self.started_at, 3)
        return SearchSnapshot(id=self.id, status=self.status, start=self.start, destination=self.destination, message=self.message, stats=self.stats.model_copy(), graph=self.graph.model_copy(deep=True), route=list(self.route), click_count=len(self.route) if self.status == "path_found" else None, data_fetched_at=dict(self.data_fetched_at), activities=list(self.activities.values()), activity_log=list(self.activity_log))

    async def report_activity(self, article: Article, update: dict) -> None:
        previous = self.activities.get(article.page_id)
        values = previous.model_dump() if previous else {"article": article}
        values.update({key: value for key, value in update.items() if key in SearchActivity.model_fields})
        activity = SearchActivity(**values)
        self.activities[article.page_id] = activity
        for node in self.graph.nodes:
            if node.article.page_id == article.page_id:
                node.phase = activity.stage
                node.expanded = activity.stage == "expanded"
                break
        message = f"{article.title}: {activity.message}"
        if not self.activity_log or self.activity_log[-1] != message:
            self.activity_log.append(message)
        await self.publish("activity", {"activities": [item.model_dump(mode="json") for item in self.activities.values()], "activity_log": list(self.activity_log), "stats": self.snapshot().stats.model_dump()})

    async def publish(self, event_type: str, data: dict) -> None:
        async with self.condition:
            self.sequence += 1
            self.events.append({"id": self.sequence, "type": event_type, "data": data})
            self.condition.notify_all()

    async def finish(self, status: str, message: str) -> None:
        if self.terminal:
            return
        self.status = status
        self.message = message
        self.activities.clear()
        for node in self.graph.nodes:
            if node.phase not in {"discovered", "expanded"}:
                node.phase = "partial" if status == "path_found" else "interrupted"
        self.stats.elapsed_seconds = round(time.monotonic() - self.started_at, 3)
        await self.publish("finished", self.snapshot().model_dump(mode="json"))


async def find_path(job: SearchJob, wikipedia: WikipediaClient) -> None:
    start = job.start
    destination = job.destination
    job.stats.discovered = 1
    await job.publish("started", job.snapshot().model_dump(mode="json"))
    if start.page_id == destination.page_id:
        await job.finish("path_found", "Start and destination are the same article: zero clicks.")
        return

    frontier = deque([start])
    visited = {start.page_id}
    parents: dict[int, ArticleLink] = {}
    sampled_nodes = {start.page_id: job.graph.nodes[0]}
    sampled_edges = set()
    completed_articles = set()
    depth = 0
    while frontier:
        if depth >= job.settings.max_depth:
            raise SearchLimit("Maximum click depth reached before finding a path.")
        job.stats.depth = depth
        next_frontier = deque()

        async def record_progress(article: Article, update: dict) -> None:
            if update.get("stage") == "expanded" and article.page_id not in completed_articles:
                completed_articles.add(article.page_id)
                job.stats.expanded += 1
            if update.get("fetched_at") is not None:
                job.data_fetched_at[str(article.page_id)] = update["fetched_at"]
            await job.report_activity(article, update)
            new_nodes = []
            new_edges = []
            found = False
            for edge in update.get("links", []):
                target = edge.destination
                if target.page_id not in visited:
                    if job.stats.discovered >= job.settings.max_discovered_articles:
                        raise SearchLimit("Discovered article limit reached.")
                    visited.add(target.page_id)
                    parents[target.page_id] = edge
                    next_frontier.append(target)
                    job.stats.discovered += 1
                    if len(sampled_nodes) < job.settings.graph_node_limit:
                        node = GraphNode(article=target, depth=depth + 1)
                        sampled_nodes[target.page_id] = node
                        job.graph.nodes.append(node)
                        new_nodes.append(node.model_dump(mode="json"))
                    else:
                        job.graph.truncated = True
                pair = (edge.source.page_id, target.page_id)
                if pair[0] in sampled_nodes and pair[1] in sampled_nodes and pair not in sampled_edges:
                    if len(sampled_edges) < job.settings.graph_node_limit * 2:
                        sampled_edges.add(pair)
                        job.graph.edges.append(edge)
                        new_edges.append(edge.model_dump(mode="json"))
                    else:
                        job.graph.truncated = True
                if target.page_id == destination.page_id:
                    found = True
                    break
            if update.get("links"):
                await job.publish("connections", {"nodes": new_nodes, "edges": new_edges, "truncated": job.graph.truncated, "stats": job.snapshot().stats.model_dump()})
            if found:
                path = []
                current = destination.page_id
                while current != start.page_id:
                    parent = parents[current]
                    path.append(parent)
                    current = parent.source.page_id
                job.route = list(reversed(path))
                job.stats.depth = len(job.route)
                raise PathFound

        async def expand(article: Article):
            async def progress(update: dict) -> None:
                await record_progress(article, update)

            neighbors = await wikipedia.neighbors(article, job.stats, progress)
            # Also handles service implementations that return a complete list directly.
            await record_progress(article, {"stage": "expanded", "message": "Article expansion complete.", "links": neighbors.links, "fetched_at": neighbors.fetched_at})
            job.activities.pop(article.page_id, None)
            await job.publish("expanded", {"article": article.model_dump(), "fetched_at": neighbors.fetched_at.isoformat(), "stats": job.snapshot().stats.model_dump()})

        while frontier:
            batch = [frontier.popleft() for _ in range(min(len(frontier), job.settings.max_concurrent_requests))]
            tasks = [asyncio.create_task(expand(article)) for article in batch]
            found = False
            try:
                await asyncio.gather(*tasks)
            except PathFound:
                found = True
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if found:
                await job.finish("path_found", "Shortest path found for the article data observed in this search.")
                return
        await job.publish("layer_completed", {"depth": depth, "stats": job.snapshot().stats.model_dump()})
        frontier = next_frontier
        depth += 1
    await job.finish("no_path", "All reachable qualifying links were exhausted without finding the destination.")


class SearchManager:
    def __init__(self, settings: Settings, wikipedia: WikipediaClient):
        self.settings = settings
        self.wikipedia = wikipedia
        self.jobs: dict[str, SearchJob] = {}
        self.start_lock = asyncio.Lock()

    async def start(self, request: SearchRequest) -> SearchJob:
        if self.start_lock.locked():
            raise SearchBusy("Another search is being initialized.")
        async with self.start_lock:
            if any(not job.terminal for job in self.jobs.values()):
                raise SearchBusy("Only one search can run at a time. Cancel it or wait for it to finish.")
            stats = SearchStats()
            started_at = time.monotonic()
            async with asyncio.timeout(self.settings.max_search_seconds):
                start, destination = await self.wikipedia.resolve_inputs(request.start, request.destination, stats)
            while len(self.jobs) >= self.settings.retained_searches:
                del self.jobs[next(iter(self.jobs))]
            job = SearchJob(start, destination, stats, self.settings, started_at)
            self.jobs[job.id] = job
            job.task = asyncio.create_task(self._run(job))
            return job

    async def _run(self, job: SearchJob) -> None:
        try:
            remaining = self.settings.max_search_seconds - (time.monotonic() - job.started_at)
            async with asyncio.timeout(max(0, remaining)):
                await find_path(job, self.wikipedia)
        except TimeoutError:
            await job.finish("limit_reached", "Search time limit reached.")
        except SearchLimit as error:
            await job.finish("limit_reached", str(error))
        except asyncio.CancelledError:
            await job.finish("cancelled", "Search cancelled.")
        except WikipediaError as error:
            await job.finish("failed", str(error))
        except Exception:
            logger.exception("Search %s failed", job.id)
            await job.finish("failed", "An unexpected backend error stopped the search.")

    async def cancel(self, job: SearchJob) -> None:
        if job.terminal:
            return
        # Stop scheduling immediately, including cancellation before _run starts.
        if job.task is not None:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        await job.finish("cancelled", "Search cancelled.")

    async def close(self) -> None:
        for job in list(self.jobs.values()):
            await self.cancel(job)

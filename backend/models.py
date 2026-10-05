"""Shared request, article, cache, and search response models."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    start: str = Field(min_length=1, max_length=500)
    destination: str = Field(min_length=1, max_length=500)


class Article(BaseModel):
    page_id: int = Field(gt=0)
    title: str


class RawLink(BaseModel):
    title: str
    anchor_text: str
    url: str


class ArticleLink(BaseModel):
    source: Article
    destination: Article
    anchor_text: str
    url: str


class Neighbors(BaseModel):
    article: Article
    links: list[ArticleLink]
    fetched_at: datetime
    revision_id: int | None = None


class SearchStats(BaseModel):
    depth: int = 0
    discovered: int = 0
    expanded: int = 0
    uncached_expansions: int = 0
    network_requests: int = 0
    cache_hits: int = 0
    metadata_cache_hits: int = 0
    elapsed_seconds: float = 0


class GraphNode(BaseModel):
    article: Article
    depth: int
    expanded: bool = False
    phase: str = "discovered"


class SearchActivity(BaseModel):
    article: Article
    stage: str
    message: str
    checked: int = 0
    total: int = 0
    accepted: int = 0
    wait_seconds: float | None = None


class GraphSample(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[ArticleLink] = Field(default_factory=list)
    truncated: bool = False


SearchStatus = Literal["running", "path_found", "limit_reached", "no_path", "cancelled", "failed"]


class SearchSnapshot(BaseModel):
    id: str
    status: SearchStatus
    start: Article
    destination: Article
    message: str
    stats: SearchStats
    graph: GraphSample
    route: list[ArticleLink] = Field(default_factory=list)
    click_count: int | None = None
    data_fetched_at: dict[str, datetime] = Field(default_factory=dict)
    activities: list[SearchActivity] = Field(default_factory=list)
    activity_log: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"

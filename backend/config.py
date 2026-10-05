"""Validated prototype settings, optionally overridden with WIKI_* variables."""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    wikipedia_api_url: str = "https://en.wikipedia.org/w/api.php"
    user_agent: str = "WikipediaPathFinder/0.1 (local development prototype)"
    cache_path: Path = PROJECT_ROOT / "data" / "cache.sqlite3"
    cache_ttl_seconds: int = Field(default=86_400, gt=0)
    max_search_seconds: float = Field(default=60, gt=0)
    max_uncached_expansions: int = Field(default=200, gt=0)
    max_discovered_articles: int = Field(default=20_000, ge=2)
    max_depth: int = Field(default=4, ge=0)
    max_concurrent_requests: int = Field(default=2, ge=1, le=2)
    request_interval_seconds: float = Field(default=0.5, ge=0)
    request_timeout_seconds: float = Field(default=15, gt=0)
    request_retries: int = Field(default=2, ge=0, le=5)
    graph_node_limit: int = Field(default=200, ge=2)
    event_history_limit: int = Field(default=128, ge=1)
    retained_searches: int = Field(default=10, ge=1)

    @classmethod
    def from_env(cls) -> "Settings":
        overrides = {name: os.environ[f"WIKI_{name.upper()}"] for name in cls.model_fields if f"WIKI_{name.upper()}" in os.environ}
        return cls(**overrides)


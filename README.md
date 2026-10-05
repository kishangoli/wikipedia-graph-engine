# Wikipedia Path Finder

Find the shortest hyperlink route between two English Wikipedia articles. See [PLAN.md](PLAN.md) for the agreed scope and navigation rules.

## Current state

The initial implementation includes a Python/FastAPI backend and a minimal frontend. Searches fetch English Wikipedia article HTML, extract qualifying prose and list links with Beautiful Soup, resolve redirects, and run depth-ordered BFS. SQLite caches article identities and complete neighbor lists. Search progress streams to the browser through Server-Sent Events.

Only one search runs at a time. A search can finish with a path, a limit, cancellation, a failure, or an exhausted reachable graph. A limit or failure does not mean no path exists.

## Run locally

Use Python 3.12 or newer. Download or clone the project, then open a terminal in its root directory (the folder containing `requirements.txt`).

On macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Then open [the app](http://127.0.0.1:8000/) in your browser. Enter article titles or English Wikipedia article URLs and click **Find path**. A simple starting pair is `Cat` → `Mammal`.

For subsequent runs on macOS or Linux, open a terminal in the project root and activate the existing environment:

```sh
source .venv/bin/activate
python -m uvicorn backend.main:app --reload
```

Stop the server with **Ctrl+C**. If port 8000 is already occupied, stop the existing server or add `--port 8001` and open port 8001 instead. Use one server worker: search jobs and progress history are held in memory.

- [Health check](http://127.0.0.1:8000/health)
- [Interactive API documentation](http://127.0.0.1:8000/docs)

The health endpoint returns `{"status":"ok"}`. It confirms application responsiveness, not Wikipedia connectivity.

## Settings and local data

Settings are in `backend/config.py`. Override them with exported `WIKI_*` environment variables before starting the server. `.env.example` documents examples; it is not loaded automatically.

Defaults are 60 seconds per search (including input validation), four clicks deep, 200 uncached article expansions, 20,000 discovered articles, two concurrent Wikimedia requests, and a 24-hour cache lifetime. The displayed graph is a capped sample and does not restrict graph traversal.

The graph uses a locally bundled D3 7.9.0 force-directed layout in a full-width sandbox, with draggable nodes, zoom/pan, a fit control, selectable labels, and connection highlighting when selecting a node. Start, destination, working, expanded, discovered, and route nodes have distinct colors. The destination is shown without invented edges until a qualifying link reaches it. The complete final route remains visible even outside the graph sample.

Validated links stream in batches while an article is still being checked. Node colors reflect working and expanded articles, and the elapsed-time display continues updating between responses. A route can finish as soon as a validated link establishes the shortest path in the current BFS layer. Partially processed neighbor lists are never written to the cache.

The database is created automatically at `data/cache.sqlite3`. Restarting keeps cached article data but loses active and recent searches. Cold searches can be slow because each expanded article's linked destinations must also be validated; repeating a completed expansion should use the cache.

An internet connection is required for uncached articles. Configure `WIKI_USER_AGENT` with a meaningful contact URL or email before shared/public use, following [Wikimedia's access policy](https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy).

## Backend structure

| File | Responsibility |
| --- | --- |
| `backend/main.py` | FastAPI application and HTTP endpoints |
| `backend/config.py` | Prototype settings and limits |
| `backend/models.py` | Shared request and article models |
| `backend/api/routes.py` | Search endpoints and SSE progress transport |
| `backend/services/wikipedia.py` | API client, pacing, retries, and identity resolution |
| `backend/services/parser.py` | Qualifying-link extraction and input normalization |
| `backend/services/cache.py` | SQLite cache |
| `backend/services/search.py` | BFS, budgets, route reconstruction, and search lifecycle |
| `backend/services/errors.py` | Expected validation, upstream, and search-limit errors |
| `frontend/` | Minimal browser page, graph drawing, and API communication |
| `tests/` | Deterministic parser, cache, search, and API checks |

## API overview

- `POST /api/searches`: accepts `start` and `destination`; validates them and returns a search ID and initial state.
- `GET /api/searches/{id}`: retrieves the current state or final result.
- `GET /api/searches/{id}/events`: streams search progress; supports `Last-Event-ID` for reconnecting.
- `POST /api/searches/{id}/cancel`: cancels an active search.

## Automated checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

These checks use saved fixtures and controlled graphs. They do not contact Wikipedia; live behavior needs a separate manual check.

## Live checks

With the server running on port 8000, use a second terminal:

```sh
source .venv/bin/activate
python -u scripts/live_check.py
```

Run these commands from the project root. On Windows, activate the environment with `.venv\Scripts\Activate.ps1` instead.

This checks health, page serving, redirect resolution, a two-step search, SSE delivery, invalid input handling, one-search-at-a-time enforcement, and cancellation. It fetches live articles and can populate the cache. Timing varies with network conditions and existing cache entries.

Observed on October 5, 2026:

| Search | Result | Backend elapsed time |
| --- | --- | --- |
| Cat → Mammal, cold article cache | One click through “mammal”; 12 upstream requests | 50.530 seconds |
| Cat → Mammal, repeated through the browser | One click; one cached expansion; zero upstream requests | 0.004 seconds |
| Domestic cat → Cat | Zero clicks after redirect resolution | 0.248 seconds |
| Ailurophobia → Mammal, with Cat cached | Two clicks: Ailurophobia → Cat → Mammal | 2.378 seconds |

These are sample measurements, not performance guarantees. Large uncached articles may spend most of the initial budget fetching and validating their linked destinations before the graph can expand.

# Wikipedia Path Finder — Initial Version Plan

## Goal

Build a small web application that finds the fewest hyperlink clicks between two English Wikipedia articles. Fetch article links during the search and display progress as the explored graph grows.

The initial version prioritizes correct link extraction, understandable search behavior, and measuring live-search performance. Most application logic lives in Python. This document defines the plan; it does not authorize implementation beyond writing this file.

## Initial scope

- Accept a starting article and a destination as an exact article title or an English Wikipedia article URL.
- Resolve and validate both inputs before searching.
- Follow existing hyperlinks attached to words or phrases in article text.
- Search using forward breadth-first search (BFS).
- Fetch and parse articles on demand, with bounded concurrency and request pacing.
- Cache extracted links in SQLite and reuse fresh cache entries across searches.
- Stream search progress to a barebones browser interface.
- Display a limited view of the explored graph and highlight the final route.
- Show the click count, article sequence, and clickable phrase used at each step.
- Support cancellation and explicit search budgets.

## Navigation and click rules

1. Only English Wikipedia articles are eligible destinations. Resolve article identity using canonical titles and page IDs where available.
2. A qualifying hyperlink that navigates to a different article costs **one click**. Multiword anchor text still represents one click.
3. Clicking a link to a redirect and landing on its canonical article costs one click total. The automatic redirect adds no extra click.
4. A link to a section of another article counts as one click to that article. Preserve its fragment for display, but use the article identity for search.
5. Same-article section links do not advance the search and are ignored.
6. Start and destination resolving to the same article produce a zero-click route.
7. Revisiting an article would still cost a click in a manual run. BFS avoids revisits because cycles cannot improve a shortest route.
8. Do not infer connections from unlinked text, semantic similarity, search results, or external websites.

### Qualifying article text

For version one, include links within the lead and main prose paragraphs, plus ordinary article-content lists. Exclude links in:

- Site navigation, page controls, and the table of contents.
- Infoboxes, tables, navigation boxes, sidebars, and maintenance notices.
- Image captions and media controls.
- Citation markers, references, bibliographies, external-link sections, and footer metadata.
- Files, categories, templates, help pages, talk pages, and other non-article namespaces.
- Missing articles and external or interlanguage destinations.

This is an intentionally narrow definition of article-text navigation. Extract links from eligible HTML containers and explicitly remove excluded structures; do not accept every anchor in the rendered page. Verify these rules against representative article HTML before treating the parser as complete.

For each accepted edge, retain the source article, canonical destination, visible anchor phrase, and original article link. Deduplicate destinations for traversal while keeping a representative qualifying phrase.

## Technology stack

| Component | Technology | Purpose |
| --- | --- | --- |
| Runtime | Python 3.12+ | Backend and application logic |
| Web backend | FastAPI | Input validation, search lifecycle, and HTTP endpoints |
| ASGI server | Uvicorn | Run the application locally |
| HTTP client | HTTPX | Asynchronous MediaWiki requests, connection reuse, and timeouts |
| Concurrency | Python asyncio | Bounded fetching, pacing, cancellation, and event delivery |
| HTML parsing | Beautiful Soup 4 with Python's built-in HTML parser | Extract allowed hyperlinks and anchor text |
| Search | collections.deque, sets, and dictionaries | BFS frontiers, visited identities, and parent pointers |
| Persistent cache | SQLite through Python sqlite3 | Store canonical article metadata and extracted neighbor lists |
| Data validation | Pydantic | Request, result, and progress-event schemas |
| Progress transport | Server-Sent Events (SSE) | Stream backend search events to the browser |
| Frontend | Plain HTML, CSS, and JavaScript | Two inputs, progress, graph display, and results |
| Graph rendering | Browser SVG with a simple depth-based layout | Display a bounded sample of nodes and directed edges |
| Verification | pytest and HTTPX test utilities | Parser fixtures, BFS correctness, cache behavior, and API tests |

Serve the frontend from FastAPI on the same origin. No React, Node build pipeline, graph database, external cache service, or AI dependency is required for this version. Pin concrete dependency versions during implementation.

All link eligibility, identity resolution, traversal, click counting, cache decisions, and path reconstruction belong in Python. JavaScript submits requests, renders streamed events, and handles basic browser interactions.

## Wikipedia integration

- Use the English Wikipedia MediaWiki Action API at `https://en.wikipedia.org/w/api.php`.
- Use article metadata queries to resolve titles, redirects, namespaces, and missing pages.
- Retrieve rendered article HTML through the Parse API and extract qualifying links with Beautiful Soup.
- Normalize relative URLs, URL encoding, fragments, and redirects without confusing anchor text with destination titles.
- Accept user URLs only for supported English Wikipedia article routes; do not fetch arbitrary user-supplied URLs.
- Use a descriptive User-Agent, bounded concurrency, pacing, request timeouts, and retry backoff. Honor upstream throttling instructions.
- Deduplicate simultaneous requests for the same article. Batch metadata lookups where supported.
- Treat malformed responses and failed fetches as errors, not as articles with no outgoing links.

## Search design

Use forward BFS on the directed graph defined by qualifying article-text links. Each edge has cost one.

1. Resolve the start and destination to canonical article identities.
2. Initialize the depth-zero frontier with the start article.
3. Load complete neighbor lists from fresh cache entries or fetch and parse them.
4. Record newly discovered identities and parent edges, including the anchor phrase.
5. Process successive depth layers until the destination is discovered, the reachable graph is exhausted, or a budget is reached.
6. Reconstruct the route by following parent pointers back to the start.

Fetch pages concurrently within a layer, but do not expand a deeper layer before the required shallower work completes. Parallel completion order must not determine route length. Multiple equally short routes may exist; returning one is sufficient.

Do not truncate neighbor lists or use semantic ranking to discard edges. Rendering limits apply only to the visualization, never to the graph being searched.

### Result guarantees

- **Path found:** BFS has established a shortest route under the configured link rules and the article data observed during this search.
- **Limit reached:** The search stopped before establishing a result. This does not mean no route exists.
- **Cancelled:** The user stopped the search.
- **Failed:** An unrecovered fetch, parsing, or validation error prevented reliable completion.
- **No path:** Report only if the reachable graph has been fully exhausted without skipped or failed expansions; this is unlikely within a small live-search budget.

Wikipedia changes over time, and a live search with caching is not a consistent historical snapshot. Show when the article data was fetched and avoid claiming a permanent shortest route on all of Wikipedia.

## SQLite cache

Store:

- Canonical article identity, title, and known aliases or redirects.
- Complete extracted neighbor lists with destination identities, anchor phrases, and links.
- Fetch timestamp and source revision identifier when available.
- A parser-rule version so changes to link eligibility invalidate incompatible entries.

Start with a configurable 24-hour cache lifetime. Reuse fresh entries, refresh stale entries, and write neighbor lists atomically only after extraction and identity resolution succeed. Do not cache transient failures as empty lists. A successfully parsed article with no qualifying links may have a valid empty cache entry.

Keep SQLite operations short and serialize writes. Avoid blocking the asynchronous search loop with substantial database work. Use in-memory state for active searches; durable job recovery is outside this version.

## API and progress events

Proposed endpoints:

- `POST /api/searches`: validate inputs and start a search; return a search ID.
- `GET /api/searches/{id}/events`: stream progress and the terminal outcome over SSE.
- `POST /api/searches/{id}/cancel`: request cancellation.
- `GET /api/searches/{id}`: retrieve the current status or final result.

Progress events should cover search start, article expansion, discovered connections, layer completion, and the terminal result. Include depth, discovered and expanded article counts, network requests, cache hits, and elapsed time.

Retain enough event history for a newly connected or reconnected browser to receive a coherent state. Batch noisy discovery events and bound history and active-search memory. Cancellation must stop scheduling new work and clean up pending tasks.

## Barebones frontend

- Start and destination inputs, a Search button, and a Cancel button.
- Validation messages and a visible search status.
- A simple directed graph grouped by search depth.
- A rendering cap, initially 200 nodes, with an indication when additional explored nodes are hidden.
- A final highlighted route, always visible even if it falls outside the graph sample.
- A route list showing article titles, qualifying anchor phrases, and click count.
- Basic performance counters for evaluating the prototype.

Use plain text rendering for extracted titles and phrases. Do not inject fetched Wikipedia HTML into the interface.

## Initial operating budgets

Use provisional, configurable defaults:

- Maximum search duration: 60 seconds.
- Maximum uncached article expansions: 200 per search.
- Maximum discovered article identities: 20,000 per search.
- Maximum BFS depth: 4 clicks.
- Maximum in-flight Wikimedia requests: 2 globally, with separate request pacing and backoff.
- One active search at a time for the local prototype.

These are experiment settings, not promises of upstream capacity or successful completion. Stop with an explicit limit-reached outcome rather than silently omitting work. Adjust budgets after measuring basic searches and checking current Wikimedia policies.

## Verification and milestones

1. **Article extraction:** validate titles, redirects, anchor text, fragments, allowed prose/list links, and excluded containers using saved HTML fixtures.
2. **Search correctness:** test synthetic directed graphs with cycles, duplicate edges, equal-length routes, no route, and out-of-order fetch completion. Confirm shortest-path and click-count behavior.
3. **Caching and lifecycle:** verify reuse, expiration, parser-version invalidation, failed fetch handling, cancellation, and budget outcomes.
4. **Minimal web flow:** connect inputs, SSE progress, graph sampling, and route display.
5. **Live experiments:** manually verify a direct-link pair, a verified two-hop pair, and a broader pair that exercises the budget. Record cold-cache and warm-cache timings, fetch counts, and extraction issues.

Initial completion means the end-to-end flow works, returned routes follow qualifying hyperlinks, BFS correctness is verified, progress is visible, and incomplete searches are reported honestly. Broad arbitrary-pair performance is a later optimization target.

## Outside initial scope

- Prebuilding or downloading Wikipedia's full graph.
- Bidirectional search and backlink validation.
- Semantic search, heuristic path pruning, or AI-generated connections.
- Multiplayer races, accounts, leaderboards, and daily challenges.
- Other Wikipedia languages or non-Wikipedia websites.
- Polished graph animation, complex graph layouts, and mobile-specific UI.
- Production deployment, distributed workers, and persistent search-job recovery.

## Reference documentation

- [MediaWiki page content and Parse API](https://www.mediawiki.org/wiki/API:Get_the_contents_of_a_page/en)
- [MediaWiki Parse API](https://www.mediawiki.org/wiki/API:Parsing_wikitext)
- [MediaWiki API etiquette](https://www.mediawiki.org/wiki/API:Etiquette/en)
- [Wikimedia API access policy](https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy)
- [FastAPI streaming responses](https://fastapi.tiangolo.com/advanced/custom-response/)

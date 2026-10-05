import asyncio

import pytest

from backend.api.routes import stream_events
from backend.models import SearchRequest
from backend.services.errors import SearchBusy


async def run_search(manager, start="Start", destination="End"):
    job = await manager.start(SearchRequest(start=start, destination=destination))
    await job.task
    return job


async def test_shortest_path_is_preserved_with_out_of_order_fetches(make_manager):
    manager = make_manager({"Start": ["Slow", "Fast"], "Slow": ["End"], "Fast": ["Middle"], "Middle": ["End"], "End": []}, delays={"Slow": 0.02})
    job = await run_search(manager)
    assert job.status == "path_found"
    assert [edge.destination.title for edge in job.route] == ["Slow", "End"]
    assert job.snapshot().click_count == 2
    assert "Middle" not in manager.wikipedia.expansions


async def test_direct_link_is_one_click(make_manager):
    job = await run_search(make_manager({"Start": ["End"], "End": []}))
    assert job.snapshot().click_count == 1
    assert job.route[0].anchor_text == "link to End"


async def test_same_article_is_zero_clicks_without_fetching(make_manager):
    manager = make_manager({"Start": []})
    job = await run_search(manager, destination="Start")
    assert job.snapshot().click_count == 0
    assert manager.wikipedia.expansions == []


async def test_cycles_duplicates_and_disconnected_graph(make_manager):
    manager = make_manager({"Start": ["A", "A"], "A": ["Start"], "End": []})
    job = await run_search(manager)
    assert job.status == "no_path"
    assert manager.wikipedia.expansions == ["Start", "A"]
    assert job.stats.discovered == 2
    assert job.snapshot().click_count is None


@pytest.mark.parametrize("limits", [{"max_depth": 1}, {"max_depth": 0}, {"max_discovered_articles": 2}, {"max_uncached_expansions": 1}])
async def test_budgets_are_not_reported_as_no_path(make_manager, limits):
    manager = make_manager({"Start": ["A", "B"], "A": ["End"], "B": [], "End": []}, **limits)
    job = await run_search(manager)
    assert job.status == "limit_reached"


async def test_fetch_failure_is_not_no_path_and_cleans_pending_fetches(make_manager):
    manager = make_manager({"Start": ["A", "B"], "A": [], "B": [], "End": []}, delays={"B": 1}, failures={"A"})
    job = await run_search(manager)
    assert job.status == "failed"
    assert manager.wikipedia.active_fetches == 0


async def test_visualization_limit_does_not_limit_traversal(make_manager):
    manager = make_manager({"Start": ["A", "B"], "A": [], "B": ["End"], "End": []}, graph_node_limit=2)
    job = await run_search(manager)
    assert job.status == "path_found"
    assert len(job.graph.nodes) == 2
    assert job.graph.truncated
    assert [edge.destination.title for edge in job.route] == ["B", "End"]


async def test_cancel_stops_pending_work_and_allows_next_search(make_manager):
    manager = make_manager({"Start": ["End"], "End": []}, delays={"Start": 1})
    job = await manager.start(SearchRequest(start="Start", destination="End"))
    await asyncio.sleep(0.01)
    with pytest.raises(SearchBusy):
        await manager.start(SearchRequest(start="Start", destination="End"))
    await manager.cancel(job)
    assert job.status == "cancelled"
    assert manager.wikipedia.active_fetches == 0
    manager.wikipedia.delays = {}
    assert (await run_search(manager)).status == "path_found"


async def test_cancel_before_search_task_starts(make_manager):
    manager = make_manager({"Start": ["End"], "End": []})
    job = await manager.start(SearchRequest(start="Start", destination="End"))
    await manager.cancel(job)
    assert job.status == "cancelled"
    assert manager.wikipedia.expansions == []


async def test_timeout_cleans_pending_fetches(make_manager):
    manager = make_manager({"Start": ["End"], "End": []}, delays={"Start": 1}, max_search_seconds=0.01)
    job = await run_search(manager)
    assert job.status == "limit_reached"
    assert manager.wikipedia.active_fetches == 0


async def test_event_history_replays_or_resets_to_snapshot(make_manager):
    manager = make_manager({"Start": ["A"], "A": ["End"], "End": []}, event_history_limit=2)
    job = await run_search(manager)
    assert len(job.events) == 2
    expired = [event async for event in stream_events(job, 0)]
    assert len(expired) == 1 and "event: snapshot" in expired[0]
    replay = [event async for event in stream_events(job, job.sequence - 1)]
    assert len(replay) == 1 and "event: finished" in replay[0]
    assert [event async for event in stream_events(job, job.sequence)] == []


async def test_multiple_sse_subscribers_receive_final_event(make_manager):
    manager = make_manager({"Start": ["End"], "End": []}, delays={"Start": 0.02})
    job = await manager.start(SearchRequest(start="Start", destination="End"))

    async def consume():
        return [event async for event in stream_events(job, None)]

    streams = await asyncio.gather(consume(), consume())
    assert all(any('"status": "path_found"' in event for event in events) for events in streams)


async def test_old_completed_searches_are_evicted(make_manager):
    manager = make_manager({"Start": ["End"], "End": []}, retained_searches=1)
    old = await run_search(manager)
    new = await run_search(manager)
    assert list(manager.jobs) == [new.id]
    assert old.id not in manager.jobs

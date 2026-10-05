import asyncio

import httpx
import pytest

from backend.main import create_app


@pytest.fixture
async def client(make_manager):
    manager = make_manager({"Start": ["End"], "End": []})
    app = create_app(manager.settings)
    async with app.router.lifespan_context(app):
        app.state.search_manager = manager
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, manager
        await manager.close()


async def test_health_frontend_and_docs(client):
    http, _ = client
    assert (await http.get("/health")).json() == {"status": "ok"}
    assert (await http.get("/")).status_code == 200
    assert (await http.get("/static/app.js")).status_code == 200
    assert (await http.get("/docs")).status_code == 200


async def test_search_result_and_terminal_sse_snapshot(client):
    http, manager = client
    response = await http.post("/api/searches", json={"start": "Start", "destination": "End"})
    assert response.status_code == 202
    search_id = response.json()["id"]
    await manager.jobs[search_id].task
    result = await http.get(f"/api/searches/{search_id}")
    assert result.json()["click_count"] == 1
    stream = await http.get(f"/api/searches/{search_id}/events")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in stream.text
    assert '"status": "path_found"' in stream.text


async def test_validation_and_missing_searches(client):
    http, _ = client
    for body in [{"start": " ", "destination": "End"}, {"start": "Start", "destination": "Missing"}, {"start": "https://example.com/wiki/Start", "destination": "End"}, {"start": "Start", "destination": "End", "extra": True}]:
        assert (await http.post("/api/searches", json=body)).status_code == 422
    for suffix in ["", "/events"]:
        assert (await http.get("/api/searches/missing" + suffix)).status_code == 404
    assert (await http.post("/api/searches/missing/cancel")).status_code == 404


async def test_busy_cancel_and_invalid_event_cursor(client):
    http, manager = client
    manager.wikipedia.delays = {"Start": 1}
    response = await http.post("/api/searches", json={"start": "Start", "destination": "End"})
    search_id = response.json()["id"]
    assert (await http.post("/api/searches", json={"start": "Start", "destination": "End"})).status_code == 409
    for cursor in ["bad", "-1"]:
        assert (await http.get(f"/api/searches/{search_id}/events", headers={"Last-Event-ID": cursor})).status_code == 400
    result = await http.post(f"/api/searches/{search_id}/cancel")
    assert result.json()["status"] == "cancelled"


async def test_validation_timeout_returns_gateway_timeout(client):
    http, manager = client
    manager.settings = manager.settings.model_copy(update={"max_search_seconds": 0.01})

    async def delayed_resolution(start, destination, stats):
        await asyncio.sleep(1)

    manager.wikipedia.resolve_inputs = delayed_resolution
    assert (await http.post("/api/searches", json={"start": "Start", "destination": "End"})).status_code == 504

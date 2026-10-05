"""Thin HTTP handlers; search decisions belong in the Python services."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.models import HealthResponse, SearchRequest, SearchSnapshot
from backend.services.errors import InvalidArticle, SearchBusy, WikipediaError
from backend.services.search import SearchJob, SearchManager


router = APIRouter()


def manager_for(request: Request) -> SearchManager:
    return request.app.state.search_manager


def job_for(request: Request, search_id: str) -> SearchJob:
    job = manager_for(request).jobs.get(search_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Search not found or no longer retained.")
    return job


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse()


@router.post("/api/searches", response_model=SearchSnapshot, status_code=202, tags=["searches"])
async def start_search(body: SearchRequest, request: Request) -> SearchSnapshot:
    try:
        job = await manager_for(request).start(body)
    except InvalidArticle as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SearchBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WikipediaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail="Input validation exceeded the search time budget.") from error
    return job.snapshot()


@router.get("/api/searches/{search_id}", response_model=SearchSnapshot, tags=["searches"])
async def get_search(search_id: str, request: Request) -> SearchSnapshot:
    return job_for(request, search_id).snapshot()


@router.post("/api/searches/{search_id}/cancel", response_model=SearchSnapshot, tags=["searches"])
async def cancel_search(search_id: str, request: Request) -> SearchSnapshot:
    job = job_for(request, search_id)
    await manager_for(request).cancel(job)
    return job.snapshot()


def encode_event(sequence: int, event_type: str, data: dict) -> str:
    return f"id: {sequence}\nevent: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_events(job: SearchJob, cursor: int | None):
    # New connections and clients that fell behind get a bounded current snapshot.
    while True:
        async with job.condition:
            oldest = job.events[0]["id"] if job.events else job.sequence + 1
            if cursor is None or cursor < oldest - 1 or cursor > job.sequence:
                cursor = job.sequence
                events = [encode_event(cursor, "snapshot", job.snapshot().model_dump(mode="json"))]
            else:
                events = [encode_event(event["id"], event["type"], event["data"]) for event in job.events if event["id"] > cursor]
                cursor = job.sequence
            terminal = job.terminal
            if not events and not terminal:
                try:
                    await asyncio.wait_for(job.condition.wait_for(lambda: job.sequence > cursor or job.terminal), timeout=15)
                except TimeoutError:
                    events = [": heartbeat\n\n"]
        for event in events:
            yield event
        if terminal:
            return


@router.get("/api/searches/{search_id}/events", tags=["searches"])
async def search_events(search_id: str, request: Request) -> StreamingResponse:
    job = job_for(request, search_id)
    header = request.headers.get("Last-Event-ID")
    try:
        cursor = int(header) if header is not None else None
        if cursor is not None and cursor < 0:
            raise ValueError
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be a nonnegative integer.") from error
    return StreamingResponse(stream_events(job, cursor), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

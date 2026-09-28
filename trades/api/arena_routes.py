"""REST and WebSocket routes for strategy simulations (the strategy "arena")."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from trades.arena.run import RunConfig, RunError, create_run, options

router = APIRouter()
ws_router = APIRouter()


class ControlBody(BaseModel):
    action: str = Field(pattern="^(play|pause|step|speed|stop)$")
    n: int = 1
    speed: float | None = None


class InjectBody(BaseModel):
    kind: str
    size: float | None = None
    bars: int | None = None
    symbol: str | None = None
    regime: str | None = None


def _runs(request: Request):
    return request.app.state.ctx.runs


def _run(request: Request, run_id: str):
    try:
        return _runs(request).get(run_id)
    except KeyError as exc:
        raise HTTPException(404, "simulation not found (simulations are kept in memory)") from exc


@router.get("/arena/options")
async def arena_options(request: Request):
    return options(request.app.state.ctx.settings.get())


@router.get("/arena/runs")
async def list_runs(request: Request):
    return _runs(request).list()


@router.post("/arena/runs")
async def start_run(request: Request, body: dict[str, Any]):
    c = request.app.state.ctx
    autoplay = bool(body.pop("autoplay", True))
    config = RunConfig.from_dict(body)
    run = await run_in_threadpool(create_run, config, c.data)
    try:
        c.runs.add(run)
    except RunError:
        await run.close()
        raise
    if autoplay:
        await run.play()
    return run.state()


@router.get("/arena/runs/{run_id}")
async def run_state(request: Request, run_id: str):
    run = _run(request, run_id)
    return await run_in_threadpool(run.state)


@router.get("/arena/runs/{run_id}/agents/{agent_id}")
async def agent_detail(request: Request, run_id: str, agent_id: str):
    run = _run(request, run_id)
    try:
        return await run_in_threadpool(run.agent_detail, agent_id)
    except KeyError as exc:
        raise HTTPException(404, f"no strategy {agent_id!r} in this simulation") from exc


@router.post("/arena/runs/{run_id}/control")
async def control(request: Request, run_id: str, body: ControlBody):
    run = _run(request, run_id)
    if body.action == "play":
        await run.play()
    elif body.action == "pause":
        await run.pause()
    elif body.action == "step":
        await run.step(body.n)
    elif body.action == "speed":
        if body.speed is None:
            raise HTTPException(400, "speed is required")
        await run.set_speed(body.speed)
    else:
        await run.stop()
    return {"status": run.status, "cursor": run.cursor, "speed": run.speed, "summary": run.summary}


@router.post("/arena/runs/{run_id}/inject")
async def inject(request: Request, run_id: str, body: InjectBody):
    run = _run(request, run_id)
    params = body.model_dump(exclude_none=True)
    kind = params.pop("kind")
    return await run.inject(kind, **params)


@router.delete("/arena/runs/{run_id}")
async def delete_run(request: Request, run_id: str):
    try:
        await _runs(request).delete(run_id)
    except KeyError as exc:
        raise HTTPException(404, "simulation not found") from exc
    return {"deleted": run_id}


@ws_router.websocket("/ws/arena/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
    await ws.accept()
    try:
        run = ws.app.state.ctx.runs.get(run_id)
    except KeyError:
        await ws.send_text(json.dumps({"type": "error", "detail": "simulation not found"}))
        await ws.close()
        return
    queue = run.subscribe()

    async def sender():
        while True:
            await ws.send_text(await queue.get())

    async def receiver():
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        run.unsubscribe(queue)

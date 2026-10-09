"""会话、任务、结果和鉴权 SSE；每次回放查询使用短生命周期 Session。"""

import asyncio
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from salesmind.api.auth import CurrentUser, require_claims, validate_session_user
from salesmind.services.auth import read_session
from salesmind.services.conversation_schemas import RunCreate, RunResume, SessionCreate
from salesmind.services.conversations import STREAM_END
from salesmind.services.task_supervisor import TaskSupervisor

router = APIRouter(prefix="/api/v1", tags=["conversations"])


async def get_supervisor(request: Request) -> TaskSupervisor:
    supervisor = request.app.state.supervisor
    await supervisor.ensure_started()
    return supervisor


Supervisor = Annotated[TaskSupervisor, Depends(get_supervisor)]


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionCreate, user: CurrentUser, supervisor: Supervisor
):
    return await supervisor.store.create_session(user.id, body.title)


@router.get("/sessions")
async def list_sessions(
    user: CurrentUser,
    supervisor: Supervisor,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return {"items": await supervisor.store.list_sessions(user.id, limit, offset)}


@router.get("/sessions/{sid}/messages")
async def history(
    sid: UUID,
    user: CurrentUser,
    supervisor: Supervisor,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return await supervisor.store.history(user.id, str(sid), limit, offset)


@router.delete("/sessions/{sid}", status_code=204)
async def delete_session(sid: UUID, user: CurrentUser, supervisor: Supervisor):
    # 活动任务必须显式取消，避免误删后仍有执行器写入。
    await supervisor.store.delete_session(user.id, str(sid))
    return Response(status_code=204)


@router.post("/sessions/{sid}/runs", status_code=202)
async def create_run(
    sid: UUID,
    body: RunCreate,
    user: CurrentUser,
    supervisor: Supervisor,
    key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
):
    return await supervisor.submit(user, str(sid), body, key)


@router.get("/runs/{rid}")
async def get_run(rid: UUID, user: CurrentUser, supervisor: Supervisor):
    return await supervisor.store.get_run(user.id, str(rid))


@router.post("/runs/{rid}/resume", status_code=202)
async def resume(rid: UUID, body: RunResume, user: CurrentUser, supervisor: Supervisor):
    return await supervisor.resume(user, str(rid), body)


@router.post("/runs/{rid}/cancel")
async def cancel(rid: UUID, user: CurrentUser, supervisor: Supervisor):
    return await supervisor.stop(user.id, str(rid))


@router.post("/runs/{rid}/pause")
async def pause(rid: UUID, user: CurrentUser, supervisor: Supervisor):
    return await supervisor.stop(user.id, str(rid), "interrupted")


@router.get("/artifacts/{aid}")
async def artifact(aid: UUID, user: CurrentUser, supervisor: Supervisor):
    return await supervisor.store.artifact(user, str(aid))


@router.get("/runs/{rid}/events")
async def events(
    rid: UUID,
    request: Request,
    user: CurrentUser,
    supervisor: Supervisor,
    claims: Annotated[dict, Depends(require_claims)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    cursor = 0
    if last_event_id is not None:
        if (
            not last_event_id.isascii()
            or not last_event_id.isdigit()
            or len(last_event_id) > 20
        ):
            raise HTTPException(400, "Last-Event-ID 必须为非负事件序号")
        cursor = int(last_event_id)
    # 在发送响应头前验证所有权与回放游标，错误保留正常 HTTP 状态。
    run, batch = await supervisor.store.events(user.id, str(rid), cursor)

    async def stream():
        nonlocal cursor, run, batch
        heartbeat_at = time.monotonic()
        auth_at = 0.0
        while not await request.is_disconnected():
            if time.time() >= claims["exp"]:
                yield ": auth-expired\n\n"
                return
            if time.monotonic() - auth_at >= supervisor.settings.sse_heartbeat_seconds:
                try:
                    record = await read_session(
                        request.app.state.redis.client, claims["sid"]
                    )
                    if record["user_id"] != user.id:
                        return
                    async with supervisor.database.app_session() as db:
                        current = await validate_session_user(
                            db, request.app.state.redis.client, claims["sid"], record
                        )
                        if current.permission_version != user.permission_version:
                            return
                except HTTPException:
                    return
                auth_at = time.monotonic()
            # 包括首次响应头后的批次，发送前再次校验授权字段与版本。
            try:
                await supervisor.store.run_scope(user.id, str(rid))
            except HTTPException:
                yield ": access-changed\n\n"
                return
            for event in batch:
                yield (
                    f"id: {event['seq']}\nevent: {event['type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
                cursor = event["seq"]
            if cursor >= run["last_event_seq"] and run["status"] in STREAM_END:
                return
            if (
                time.monotonic() - heartbeat_at
                >= supervisor.settings.sse_heartbeat_seconds
            ):
                yield ": heartbeat\n\n"
                heartbeat_at = time.monotonic()
            await asyncio.sleep(supervisor.settings.sse_poll_seconds)
            try:
                run, batch = await supervisor.store.events(user.id, str(rid), cursor)
            except HTTPException:
                yield ": access-changed\n\n"
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )

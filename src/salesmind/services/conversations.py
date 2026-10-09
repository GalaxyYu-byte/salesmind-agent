"""会话、任务、可见消息和可回放事件的事务边界。"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select

from salesmind.db.entities import (
    AnalysisRun,
    AppUser,
    ChatMessage,
    ChatSession,
    ResultArtifact,
    RunEvent,
    ToolExecution,
)
from salesmind.db.session import Database
from salesmind.services.analysis import AnalysisResult
from salesmind.services.conversation_schemas import AnalysisSpec, RunCreate, RunResume
from salesmind.services.intent import IntentDecision
from salesmind.services.permissions import DataScope, load_data_scope
from salesmind.services.sales_overview import OverviewResponse

EXECUTOR_VERSION = "overview-v1"
METRIC_VERSION = "sales-v1"
QUERY_CHECKPOINT_KEY = "checkpoint:query"
QUERY_CHECKPOINT_TYPE = "sales.query-checkpoint.v1"
TERMINAL = {"completed", "partial", "failed", "cancelled"}
STREAM_END = TERMINAL | {"awaiting_input", "interrupted"}
ACCESS_CHANGED = "账号或权限已变化，请重新分析"


def now() -> datetime:
    """数据库统一写 UTC 的无时区时间，与现有 MySQL DATETIME 对齐。"""
    return datetime.now(UTC).replace(tzinfo=None)


def timestamp(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def session_json(row: ChatSession) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "updated_at": timestamp(row.updated_at),
        "active_run_id": row.active_run_id,
    }


def run_json(row: AnalysisRun) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "status": row.status,
        "stage": row.stage,
        "attempt": row.attempt,
        "request": {k: v for k, v in row.request_json.items() if not k.startswith("_")},
        "clarification_fields": row.request_json.get("_clarification_fields", []),
        "clarification_replies": row.request_json.get("_clarification_replies", []),
        "context_run_id": row.request_json.get("_context_run_id"),
        "inherited_fields": row.request_json.get("_inherited_fields", []),
        "metric_resolution": row.request_json.get("_metric_resolution"),
        "plan": row.request_json.get("_plan", []),
        "last_event_seq": row.last_event_seq,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "active_elapsed_ms": row.active_elapsed_ms,
    }


def unavailable_run_json(row: AnalysisRun) -> dict:
    """保留用户原问题和任务控制状态，清除旧授权下产生的内容。"""
    result = run_json(row)
    result.update(
        request={
            "question": row.request_json["question"],
            "mode": "unsupported",
            "explanation": ACCESS_CHANGED,
        },
        clarification_fields=[],
        clarification_replies=[],
        context_run_id=None,
        inherited_fields=[],
        metric_resolution=None,
        plan=[],
        error_code="access_changed",
        error_message=ACCESS_CHANGED,
        artifact_id=None,
    )
    return result


def event_json(row: RunEvent, session_id: str) -> dict:
    return {
        "schema_version": row.schema_version,
        "run_id": row.run_id,
        "session_id": session_id,
        "seq": row.seq,
        "timestamp": timestamp(row.created_at),
        "type": row.event_type,
        "payload": row.payload_json,
    }


class Conversations:
    """单实例写入串行化；数据库行锁及唯一约束同时保留。"""

    def __init__(self, database: Database):
        self.database = database
        self.lock = asyncio.Lock()

    async def owned_session(self, db, owner: str, sid: str, *, lock=False):
        query = select(ChatSession).where(
            ChatSession.id == sid, ChatSession.owner_id == owner
        )
        row = await db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "会话不存在")
        return row

    async def owned_run(self, db, owner: str, rid: str, *, lock=False):
        query = select(AnalysisRun).where(
            AnalysisRun.id == rid, AnalysisRun.owner_id == owner
        )
        row = await db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "任务不存在")
        return row

    async def current_scope(self, db, owner: str) -> DataScope:
        """每个执行或输出阶段读取当前授权，避免信任登录时的用户快照。"""
        user = await db.get(AppUser, owner)
        if user is None or not user.active:
            raise HTTPException(409, ACCESS_CHANGED)
        async with self.database.sales_session() as sales:
            return await load_data_scope(db, sales, user)

    @staticmethod
    def scope_matches(run: AnalysisRun | ResultArtifact, scope: DataScope) -> bool:
        return (
            run.permission_version == scope.permission_version
            and run.scope_hash == scope.scope_hash
        )

    async def verify_run_scope(self, db, run: AnalysisRun) -> DataScope:
        scope = await self.current_scope(db, run.owner_id)
        if not self.scope_matches(run, scope):
            raise HTTPException(409, ACCESS_CHANGED)
        return scope

    async def run_scope(self, owner: str, rid: str) -> DataScope:
        async with self.database.app_session() as db:
            run = await self.owned_run(db, owner, rid)
            return await self.verify_run_scope(db, run)

    def event(self, db, run: AnalysisRun, kind: str, payload: dict):
        run.last_event_seq += 1
        run.updated_at = now()
        db.add(
            RunEvent(
                run_id=run.id,
                seq=run.last_event_seq,
                schema_version=1,
                event_type=kind,
                payload_json=payload,
                created_at=run.updated_at,
            )
        )

    def message(self, db, run: AnalysisRun, role: str, kind: str, content: str):
        run.last_message_seq += 1
        db.add(
            ChatMessage(
                id=str(uuid4()),
                run_id=run.id,
                session_id=run.session_id,
                owner_id=run.owner_id,
                seq=run.last_message_seq,
                message_key=f"{kind}:{run.last_message_seq}",
                role=role,
                kind=kind,
                content=content,
                created_at=now(),
            )
        )

    async def release(self, db, run: AnalysisRun):
        row = await self.owned_session(db, run.owner_id, run.session_id, lock=True)
        if row.active_run_id == run.id:
            row.active_run_id = None
        row.updated_at = now()

    async def create_session(self, owner: str, title: str) -> dict:
        async with self.lock, self.database.app_session() as db, db.begin():
            row = ChatSession(
                id=str(uuid4()),
                owner_id=owner,
                title=title,
                graph_version=EXECUTOR_VERSION,
                created_at=now(),
                updated_at=now(),
            )
            db.add(row)
            await db.flush()
            return session_json(row)

    async def list_sessions(self, owner: str, limit: int, offset: int) -> list[dict]:
        async with self.database.app_session() as db:
            rows = await db.scalars(
                select(ChatSession)
                .where(ChatSession.owner_id == owner)
                .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return [session_json(row) for row in rows]

    async def history(self, owner: str, sid: str, limit: int, offset: int) -> dict:
        async with self.database.app_session() as db:
            row = await self.owned_session(db, owner, sid)
            try:
                scope = await self.current_scope(db, owner)
            except HTTPException:
                scope = None
            runs = list(
                await db.scalars(
                    select(AnalysisRun)
                    .where(
                        AnalysisRun.session_id == sid,
                        AnalysisRun.owner_id == owner,
                    )
                    .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            ids = [run.id for run in runs]
            messages = list(
                await db.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.run_id.in_(ids), ChatMessage.owner_id == owner)
                    .order_by(ChatMessage.created_at, ChatMessage.seq)
                )
            )
            visible_ids = {
                run.id for run in runs if scope and self.scope_matches(run, scope)
            }
            visible_messages = [
                {
                    "id": m.id,
                    "run_id": m.run_id,
                    "seq": m.seq,
                    "role": m.role,
                    "kind": m.kind,
                    "content": m.content,
                }
                for m in messages
                if m.run_id in visible_ids
                or (m.role == "user" and m.kind == "question")
            ]
            for run in reversed(runs):
                if run.id not in visible_ids:
                    visible_messages.append(
                        {
                            "id": f"access-changed:{run.id}",
                            "run_id": run.id,
                            "seq": run.last_message_seq + 1,
                            "role": "assistant",
                            "kind": "error",
                            "content": ACCESS_CHANGED,
                        }
                    )
            return {
                "session": session_json(row),
                "runs": [
                    run_json(run)
                    if run.id in visible_ids
                    else unavailable_run_json(run)
                    for run in reversed(runs)
                ],
                "messages": visible_messages,
            }

    async def create_run(
        self,
        user: AppUser,
        sid: str,
        body: RunCreate,
        key: str,
        capacity: Callable[[], bool],
    ) -> tuple[dict, bool]:
        request = body.model_dump(mode="json")
        digest = fingerprint({"session_id": sid, "request": request})
        async with self.lock, self.database.app_session() as db, db.begin():
            chat = await self.owned_session(db, user.id, sid, lock=True)
            scope = await self.current_scope(db, user.id)
            scope.require_data()
            if body.mode == "analysis":
                scope.require_analysis(body.analysis)
            existing = await db.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.owner_id == user.id, AnalysisRun.request_id == key
                )
            )
            if existing is not None:
                if existing.input_hash != digest:
                    raise HTTPException(409, "相同 Idempotency-Key 不能用于不同请求")
                await self.verify_run_scope(db, existing)
                return run_json(existing), False
            if chat.active_run_id is not None:
                raise HTTPException(409, "此会话已有未结束任务，请先继续或取消")
            if body.mode in {"auto", "analysis"} and not capacity():
                raise HTTPException(
                    429, "分析任务队列已满，请稍后重试", headers={"Retry-After": "2"}
                )
            if body.mode == "auto":
                previous = await db.scalar(
                    select(AnalysisRun)
                    .where(
                        AnalysisRun.session_id == sid,
                        AnalysisRun.owner_id == user.id,
                        AnalysisRun.status == "completed",
                        AnalysisRun.permission_version == scope.permission_version,
                        AnalysisRun.metric_version == METRIC_VERSION,
                        AnalysisRun.scope_hash == scope.scope_hash,
                    )
                    .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
                    .limit(1)
                )
                if previous:
                    request["_context_run_id"] = previous.id
            run = AnalysisRun(
                id=str(uuid4()),
                session_id=sid,
                owner_id=user.id,
                request_id=key,
                input_hash=digest,
                request_json=request,
                status="queued",
                attempt=0,
                reference_date=datetime.now(timezone(timedelta(hours=8))).date(),
                timezone="Asia/Shanghai",
                graph_version=EXECUTOR_VERSION,
                metric_version=METRIC_VERSION,
                scope_hash=scope.scope_hash,
                permission_version=scope.permission_version,
                last_event_seq=0,
                last_message_seq=0,
                replay_floor_seq=0,
                node_steps=0,
                tool_calls=0,
                model_calls=0,
                active_elapsed_ms=0,
                created_at=now(),
                updated_at=now(),
            )
            db.add(run)
            # 先写任务，再建立会话的循环外键。
            await db.flush()
            chat.active_run_id = run.id
            chat.title = body.question[:200] if chat.title == "新的分析" else chat.title
            chat.updated_at = now()
            self.message(db, run, "user", "question", body.question)
            self.event(db, run, "run.queued", {"status": "queued"})
            if body.mode == "clarify":
                run.request_json = {
                    **run.request_json,
                    "_clarification_fields": ["months"],
                }
                run.status = "awaiting_input"
                note = body.explanation or "请选择完整月份范围，再继续分析。"
                self.message(db, run, "assistant", "clarification", note)
                self.event(
                    db,
                    run,
                    "clarification.required",
                    {"message": note, "fields": ["months"]},
                )
            elif body.mode == "unsupported":
                note = body.explanation or "当前分析暂不支持此问题。"
                run.status, run.finished_at = "failed", now()
                run.error_code, run.error_message = "unsupported", note
                self.message(db, run, "assistant", "error", note)
                self.event(
                    db, run, "run.failed", {"message": note, "code": "unsupported"}
                )
                await self.release(db, run)
            return run_json(run), body.mode in {"auto", "analysis"}

    async def get_run(self, owner: str, rid: str) -> dict:
        async with self.database.app_session() as db:
            run = await self.owned_run(db, owner, rid)
            try:
                await self.verify_run_scope(db, run)
            except HTTPException:
                return unavailable_run_json(run)
            result = run_json(run)
            artifact = await db.scalar(
                select(ResultArtifact.id).where(
                    ResultArtifact.run_id == rid,
                    ResultArtifact.owner_id == owner,
                    ResultArtifact.artifact_key == "overview",
                )
            )
            result["artifact_id"] = artifact
            return result

    async def artifact(self, user: AppUser, aid: str) -> dict:
        async with self.database.app_session() as db:
            row = await db.scalar(
                select(ResultArtifact).where(
                    ResultArtifact.id == aid, ResultArtifact.owner_id == user.id
                )
            )
            if row is None:
                raise HTTPException(404, "结果不存在")
            scope = await self.current_scope(db, user.id)
            if not self.scope_matches(row, scope):
                raise HTTPException(409, ACCESS_CHANGED)
            if row.expires_at <= now():
                raise HTTPException(410, "结果快照已过期，请重新分析")
            return {
                "id": row.id,
                "kind": row.kind,
                "schema": row.schema_json,
                "payload": row.payload_json,
            }

    async def events(self, owner: str, rid: str, after: int) -> tuple[dict, list[dict]]:
        async with self.database.app_session() as db:
            run = await self.owned_run(db, owner, rid)
            await self.verify_run_scope(db, run)
            if after < run.replay_floor_seq:
                raise HTTPException(410, "事件游标已过期，请读取任务状态重建结果")
            if after > run.last_event_seq:
                raise HTTPException(400, "事件游标超过任务最后序号")
            rows = await db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == rid, RunEvent.seq > after)
                .order_by(RunEvent.seq)
                .limit(100)
            )
            return run_json(run), [event_json(row, run.session_id) for row in rows]

    async def begin_attempt(self, owner: str, rid: str) -> dict | None:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "queued":
                return None
            scope = await self.verify_run_scope(db, run)
            scope.require_data()
            if run.request_json.get("analysis") is not None:
                scope.require_analysis(
                    AnalysisSpec.model_validate(run.request_json["analysis"])
                )
            run.status, run.attempt, run.started_at = "running", run.attempt + 1, now()
            self.event(db, run, "run.started", {"attempt": run.attempt})
            return run_json(run)

    async def _query_checkpoint(self, db, run: AnalysisRun) -> OverviewResponse | None:
        """读取查询存档；有存档标记却无法还原时拒绝静默重新查询。"""
        row = await db.scalar(
            select(ResultArtifact).where(
                ResultArtifact.run_id == run.id,
                ResultArtifact.owner_id == run.owner_id,
                ResultArtifact.artifact_key == QUERY_CHECKPOINT_KEY,
            )
        )
        checkpoint_id = run.request_json.get("_query_checkpoint_id")
        if row is None and checkpoint_id is None:
            return None
        invalid = HTTPException(409, "查询检查点不可用，请取消后重新分析")
        if row is None or row.id != checkpoint_id:
            raise invalid
        if row.expires_at <= now():
            raise HTTPException(410, "查询检查点已过期，请取消后重新分析")
        if (
            run.graph_version != EXECUTOR_VERSION
            or run.metric_version != METRIC_VERSION
            or row.session_id != run.session_id
            or row.scope_hash != run.scope_hash
            or row.permission_version != run.permission_version
            or row.metric_version != run.metric_version
            or row.data_version != run.data_version
            or row.kind != "DATASET"
            or row.schema_version != 1
        ):
            raise invalid
        try:
            analysis = AnalysisSpec.model_validate(run.request_json["analysis"])
            schema = row.schema_json
            if (
                run.request_json["mode"] != "analysis"
                or schema.get("type") != QUERY_CHECKPOINT_TYPE
                or schema.get("next_node") != "analyze"
                or schema.get("graph_version") != run.graph_version
                or schema.get("analysis_hash")
                != fingerprint(analysis.model_dump(mode="json"))
                or schema.get("payload_hash") != fingerprint(row.payload_json)
            ):
                raise invalid
            overview = OverviewResponse.model_validate(row.payload_json)
        except (ValidationError, KeyError, TypeError, AttributeError, ValueError):
            raise invalid from None
        if overview.dataset.version != row.data_version:
            raise invalid
        return overview

    async def restore_query(
        self, owner: str, rid: str, attempt: int
    ) -> OverviewResponse | None:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or run.attempt != attempt:
                raise asyncio.CancelledError
            await self.verify_run_scope(db, run)
            return await self._query_checkpoint(db, run)

    async def checkpoint_query(
        self,
        owner: str,
        rid: str,
        attempt: int,
        result: OverviewResponse,
        retention: int,
    ) -> bool:
        """查询输出与完成事件原子提交，恢复时跳过已完成的查询。"""
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or run.attempt != attempt:
                return False
            scope = await self.verify_run_scope(db, run)
            if await self._query_checkpoint(db, run) is not None:
                return True
            analysis = AnalysisSpec.model_validate(run.request_json["analysis"])
            scope.require_analysis(analysis)
            payload = result.model_dump(mode="json", exclude_none=True)
            schema = {
                "type": QUERY_CHECKPOINT_TYPE,
                "next_node": "analyze",
                "graph_version": run.graph_version,
                "analysis_hash": fingerprint(analysis.model_dump(mode="json")),
                "payload_hash": fingerprint(payload),
            }
            size = len(
                json.dumps(
                    {"schema": schema, "payload": payload}, ensure_ascii=False
                ).encode()
            )
            if size > 1048576:
                raise ValueError("查询结果超过单条快照上限")
            aid, saved_at = str(uuid4()), now()
            db.add(
                ResultArtifact(
                    id=aid,
                    run_id=rid,
                    session_id=run.session_id,
                    owner_id=owner,
                    artifact_key=QUERY_CHECKPOINT_KEY,
                    kind="DATASET",
                    schema_version=1,
                    schema_json=schema,
                    payload_json=payload,
                    scope_hash=run.scope_hash,
                    permission_version=run.permission_version,
                    data_version=result.dataset.version,
                    metric_version=run.metric_version,
                    row_count=len(result.dataset.cells),
                    byte_size=size,
                    created_at=saved_at,
                    expires_at=saved_at + timedelta(days=retention),
                )
            )
            run.request_json = {**run.request_json, "_query_checkpoint_id": aid}
            run.data_version = result.dataset.version
            run.stage = "query"
            run.node_steps += 1
            run.tool_calls += 1
            self.event(db, run, "stage.completed", {"stage": "query"})
            return True

    async def stage(
        self,
        owner: str,
        rid: str,
        stage: str,
        completed=False,
        *,
        attempt: int | None = None,
    ) -> bool:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or (
                attempt is not None and run.attempt != attempt
            ):
                return False
            scope = await self.verify_run_scope(db, run)
            if run.request_json.get("analysis") is not None:
                scope.require_analysis(
                    AnalysisSpec.model_validate(run.request_json["analysis"])
                )
            run.stage = stage
            if completed:
                run.node_steps += 1
            self.event(
                db,
                run,
                "stage.completed" if completed else "stage.started",
                {"stage": stage},
            )
            return True

    async def intent_input(
        self, owner: str, rid: str, *, attempt: int | None = None
    ) -> dict | None:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or (
                attempt is not None and run.attempt != attempt
            ):
                return None
            await self.verify_run_scope(db, run)
            run.model_calls += 1
            previous = None
            context_id = run.request_json.get("_context_run_id")
            if context_id:
                source = await db.get(AnalysisRun, context_id)
                if (
                    source
                    and source.owner_id == owner
                    and source.session_id == run.session_id
                    and source.status == "completed"
                    and source.permission_version == run.permission_version
                    and source.scope_hash == run.scope_hash
                    and source.metric_version == run.metric_version
                ):
                    previous = AnalysisSpec.model_validate(
                        source.request_json["analysis"]
                    ).model_dump(mode="json")
            return {
                "question": run.request_json["question"],
                "replies": run.request_json.get("_clarification_replies", []),
                "reference_date": run.reference_date,
                "timezone": run.timezone,
                "previous_analysis": previous,
            }

    async def plan(
        self, owner: str, rid: str, steps: list[dict], *, attempt: int | None = None
    ) -> bool:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or (
                attempt is not None and run.attempt != attempt
            ):
                return False
            await self.verify_run_scope(db, run)
            run.request_json = {**run.request_json, "_plan": steps}
            self.event(db, run, "plan.created", {"steps": steps})
            return True

    async def apply_intent(
        self,
        owner: str,
        rid: str,
        decision: IntentDecision,
        *,
        attempt: int | None = None,
    ) -> bool:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or (
                attempt is not None and run.attempt != attempt
            ):
                return False
            scope = await self.verify_run_scope(db, run)
            if decision.kind == "ready":
                scope.require_analysis(decision.analysis)
                run.request_json = {
                    **run.request_json,
                    "mode": "analysis",
                    "analysis": decision.analysis.model_dump(mode="json"),
                    "explanation": None,
                    "_clarification_fields": [],
                    "_inherited_fields": list(decision.inherited_fields),
                    "_metric_resolution": decision.metric_resolution,
                }
                self.event(
                    db,
                    run,
                    "intent.resolved",
                    {
                        "analysis": run.request_json["analysis"],
                        "inherited_fields": list(decision.inherited_fields),
                        "metric_resolution": decision.metric_resolution,
                    },
                )
                return True
            if decision.kind != "clarify":
                raise ValueError("不支持的意图必须通过失败状态结束")
            run.request_json = {
                **run.request_json,
                "_clarification_fields": list(decision.fields),
                "_metric_resolution": decision.metric_resolution,
            }
            run.status = "awaiting_input"
            run.error_message = decision.message[:500]
            self.message(db, run, "assistant", "clarification", run.error_message)
            self.event(
                db,
                run,
                "clarification.required",
                {
                    "message": run.error_message,
                    "fields": list(decision.fields),
                    "metric_resolution": decision.metric_resolution,
                },
            )
            return False

    async def complete(
        self,
        owner: str,
        rid: str,
        result: OverviewResponse,
        retention: int,
        analysis_result: dict | None = None,
        *,
        attempt: int | None = None,
    ):
        payload = result.model_dump(mode="json", exclude_none=True)
        schema = {"type": "sales.overview.v1"}
        if analysis_result is not None:
            analysis_result = AnalysisResult.model_validate(analysis_result).model_dump(
                mode="json"
            )
            payload["analysis_result"] = analysis_result
            schema = {"type": "sales.analysis.v1"}
        size = len(
            json.dumps(
                {"schema": schema, "payload": payload}, ensure_ascii=False
            ).encode()
        )
        if size > 1048576:
            raise ValueError("查询结果超过单条快照上限")
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status != "running" or (
                attempt is not None and run.attempt != attempt
            ):
                return
            # 执行结束时重新读取账号，拒绝执行期间失效的权限快照。
            scope = await self.verify_run_scope(db, run)
            scope.require_analysis(
                AnalysisSpec.model_validate(run.request_json["analysis"])
            )
            schema["analysis"] = run.request_json["analysis"]
            payload["access"] = scope.public()
            size = len(
                json.dumps(
                    {"schema": schema, "payload": payload}, ensure_ascii=False
                ).encode()
            )
            if size > 1048576:
                raise ValueError("查询结果超过单条快照上限")
            aid = str(uuid4())
            db.add(
                ResultArtifact(
                    id=aid,
                    run_id=rid,
                    session_id=run.session_id,
                    owner_id=owner,
                    artifact_key="overview",
                    kind="DATASET",
                    schema_version=1,
                    schema_json=schema,
                    payload_json=payload,
                    scope_hash=run.scope_hash,
                    permission_version=run.permission_version,
                    data_version=result.dataset.version,
                    metric_version=run.metric_version,
                    row_count=len(result.dataset.cells),
                    byte_size=size,
                    created_at=now(),
                    expires_at=now() + timedelta(days=retention),
                )
            )
            run.data_version = result.dataset.version
            if "_query_checkpoint_id" not in run.request_json:
                run.tool_calls += 1
            self.event(
                db,
                run,
                "result.ready",
                {"artifact_id": aid, "row_count": len(result.dataset.cells)},
            )
            note = (
                "查询完成："
                + "，".join(
                    text
                    for metric, text in (
                        ("sales_amount", f"销售额 {result.totals.sales_amount} 元"),
                        ("gross_profit", f"毛利 {result.totals.gross_profit} 元"),
                        ("order_count", f"已完成订单 {result.totals.order_count} 笔"),
                    )
                    if metric in scope.allowed_metrics
                )
                + "。"
            )
            if analysis_result:
                note = analysis_result["headline"]
            self.message(db, run, "assistant", "answer", note)
            run.status, run.finished_at = "completed", now()
            self.event(db, run, "run.completed", {"artifact_id": aid})
            await self.release(db, run)

    async def fail(
        self,
        owner: str,
        rid: str,
        code: str,
        message: str,
        *,
        metric_resolution=None,
        attempt: int | None = None,
    ):
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status not in {"queued", "running"} or (
                attempt is not None
                and (run.attempt != attempt or run.status != "running")
            ):
                return
            run.status, run.finished_at = "failed", now()
            run.error_code, run.error_message = code, message[:500]
            if metric_resolution is not None:
                run.request_json = {
                    **run.request_json,
                    "_metric_resolution": metric_resolution,
                }
            self.message(db, run, "assistant", "error", run.error_message)
            self.event(
                db,
                run,
                "run.failed",
                {
                    "code": code,
                    "message": run.error_message,
                    "metric_resolution": metric_resolution,
                },
            )
            await self.release(db, run)

    async def request_stop(self, owner: str, rid: str) -> dict:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status not in TERMINAL:
                run.status = "cancelling"
                self.event(db, run, "run.cancelling", {})
            return run_json(run)

    async def finish_stop(
        self, owner: str, rid: str, target: str, *, attempt: int | None = None
    ) -> dict:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, owner, rid, lock=True)
            if run.status in TERMINAL or (
                attempt is not None
                and (
                    run.attempt != attempt
                    or run.status not in {"running", "cancelling"}
                )
            ):
                return run_json(run)
            if run.status == "interrupted" and target == "interrupted":
                return run_json(run)
            run.status = target
            run.finished_at = now() if target == "cancelled" else None
            note = (
                "任务已取消。"
                if target == "cancelled"
                else "任务已暂停；继续时从已保存的查询结果接着计算。"
                if run.request_json.get("_query_checkpoint_id")
                else "任务已暂停；查询尚未保存，继续时按原范围重新查询。"
            )
            run.error_message = note
            self.event(db, run, f"run.{target}", {"message": note})
            if target == "cancelled":
                await self.release(db, run)
            # 同一毫秒内更新时间可能未被标记为修改；flush 后该服务器列会过期。
            # 返回状态前显式异步读取，避免序列化触发同步数据库 IO。
            await db.flush()
            await db.refresh(run, attribute_names=["updated_at"])
            return run_json(run)

    async def resume(
        self, user: AppUser, rid: str, body: RunResume, capacity: Callable[[], bool]
    ) -> dict:
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await self.owned_run(db, user.id, rid, lock=True)
            if run.status not in {"awaiting_input", "interrupted"}:
                raise HTTPException(409, "只有等待补充或已暂停的任务可以继续")
            scope = await self.verify_run_scope(db, run)
            scope.require_data()
            if (
                run.graph_version != EXECUTOR_VERSION
                or run.metric_version != METRIC_VERSION
            ):
                raise HTTPException(409, "任务权限或执行器版本已变化，请取消后重新分析")
            if not capacity():
                raise HTTPException(429, "分析任务队列已满，请稍后重试")
            if run.status == "awaiting_input":
                if run.request_json["mode"] == "auto" or (
                    body.reply is not None and body.analysis is None
                ):
                    if body.reply is None or body.analysis is not None:
                        raise HTTPException(
                            422, "请提供补充回答，由后端重新解析分析条件"
                        )
                    replies = run.request_json.get("_clarification_replies", [])
                    if len(replies) >= 5:
                        raise HTTPException(
                            409, "补充次数已达上限，请取消后提交完整问题"
                        )
                    run.request_json = {
                        **run.request_json,
                        "mode": "auto",
                        "_clarification_replies": [*replies, body.reply],
                        "_clarification_fields": [],
                    }
                else:
                    if body.analysis is None:
                        raise HTTPException(422, "请补充完整分析范围")
                    scope.require_analysis(body.analysis)
                    run.request_json = {
                        **run.request_json,
                        "mode": "analysis",
                        "analysis": body.analysis.model_dump(mode="json"),
                        "explanation": None,
                        "_clarification_fields": [],
                    }
                self.message(
                    db,
                    run,
                    "user",
                    "clarification_reply",
                    body.reply or "已补充分析范围",
                )
            elif body.analysis is not None or body.reply is not None:
                raise HTTPException(409, "恢复任务保留原始范围；修改条件请创建新任务")
            else:
                await self._query_checkpoint(db, run)
            run.status, run.error_message, run.error_code = "queued", None, None
            self.event(db, run, "run.queued", {"resumed": True})
            return run_json(run)

    async def recover(self):
        """仅唯一实例启动时调用，将旧进程留下的执行记录转成显式恢复状态。"""
        async with self.lock, self.database.app_session() as db, db.begin():
            runs = await db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.status.in_(["queued", "running", "cancelling"]))
                .with_for_update()
            )
            for run in runs:
                run.status, run.finished_at = "interrupted", None
                run.error_message = "服务重启使执行中断，请继续任务或取消。"
                self.event(db, run, "run.interrupted", {"message": run.error_message})

    async def record_elapsed(self, owner: str, rid: str, attempt: int, elapsed: int):
        async with self.lock, self.database.app_session() as db, db.begin():
            run = await db.scalar(
                select(AnalysisRun)
                .where(AnalysisRun.id == rid, AnalysisRun.owner_id == owner)
                .with_for_update()
            )
            if run is not None and run.attempt == attempt:
                run.active_elapsed_ms += elapsed

    async def delete_session(self, owner: str, sid: str):
        async with self.lock, self.database.app_session() as db, db.begin():
            chat = await self.owned_session(db, owner, sid, lock=True)
            if chat.active_run_id is not None:
                raise HTTPException(409, "请先取消未结束任务，再删除会话")
            ids = select(AnalysisRun.id).where(
                AnalysisRun.session_id == sid, AnalysisRun.owner_id == owner
            )
            for model in (RunEvent, ToolExecution, ChatMessage, ResultArtifact):
                await db.execute(delete(model).where(model.run_id.in_(ids)))
            await db.execute(
                delete(AnalysisRun).where(
                    AnalysisRun.session_id == sid, AnalysisRun.owner_id == owner
                )
            )
            await db.delete(chat)

"""Analysis runs, events, and tool executions."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, SMALLINT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from salesmind.db.base import AgentAppBase
from salesmind.db.entities.agent_app_common import (
    _ascii_char,
    _ascii_varchar,
    _created_at,
    _updated_at,
)

if TYPE_CHECKING:
    from salesmind.db.entities.agent_app_artifacts import ResultArtifact
    from salesmind.db.entities.agent_app_sessions import ChatMessage, ChatSession


class AnalysisRun(AgentAppBase):
    """一次独立分析请求；澄清、恢复和重试仍归属同一任务。"""

    __tablename__ = "analysis_run"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uk_run_owner_request"),
        UniqueConstraint("id", "session_id", "owner_id", name="uk_run_session_owner"),
        Index("idx_run_session_created", "session_id", "created_at", "id"),
        Index("idx_run_session_owner_fk", "session_id", "owner_id"),
        Index("idx_run_status_updated", "status", "updated_at"),
        Index("idx_run_paused_until", "paused_until"),
        ForeignKeyConstraint(
            ["session_id", "owner_id"],
            ["agent_app.chat_session.id", "agent_app.chat_session.owner_id"],
            name="fk_run_session_owner",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_input', 'interrupted', 'cancelling', 'completed', 'partial', 'failed', 'cancelled')",
            name="ck_run_status",
        ),
        CheckConstraint("permission_version > 0", name="ck_run_permission"),
        CheckConstraint(
            "data_version IS NULL OR data_version > 0", name="ck_run_data_version"
        ),
        CheckConstraint(
            "replay_floor_seq <= last_event_seq", name="ck_run_replay_floor"
        ),
        CheckConstraint(
            "(status IN ('completed', 'partial', 'failed', 'cancelled') AND finished_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'partial', 'failed', 'cancelled') AND finished_at IS NULL)",
            name="ck_run_finished",
        ),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "一次独立分析请求；澄清和恢复继续使用同一任务",
        },
    )

    id: Mapped[str] = mapped_column(_ascii_char(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    # 同一用户的 request_id 唯一，用于请求幂等。
    request_id: Mapped[str] = mapped_column(_ascii_varchar(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(_ascii_char(64), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        _ascii_varchar(24), nullable=False, server_default=text("'queued'")
    )
    stage: Mapped[str | None] = mapped_column(_ascii_varchar(32))
    attempt: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    reference_date: Mapped[date] = mapped_column(nullable=False)
    timezone: Mapped[str] = mapped_column(
        _ascii_varchar(64), nullable=False, server_default=text("'Asia/Shanghai'")
    )
    graph_version: Mapped[str] = mapped_column(_ascii_varchar(32), nullable=False)
    metric_version: Mapped[str] = mapped_column(_ascii_varchar(32), nullable=False)
    # 固定创建时的权限与数据版本，恢复任务时不能直接覆盖为当前值。
    scope_hash: Mapped[str] = mapped_column(_ascii_char(64), nullable=False)
    permission_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    data_version: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    # 事件、消息序号与回放下界分别维护，避免续播时遗漏或重复。
    last_event_seq: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )
    last_message_seq: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )
    replay_floor_seq: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )
    node_steps: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    tool_calls: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    model_calls: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    active_elapsed_ms: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(_ascii_varchar(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    paused_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    session: Mapped[ChatSession] = relationship(
        back_populates="runs",
        foreign_keys=[session_id, owner_id],
        lazy="raise",
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="run",
        foreign_keys="ChatMessage.run_id,ChatMessage.session_id,ChatMessage.owner_id",
        lazy="raise",
    )
    events: Mapped[list[RunEvent]] = relationship(back_populates="run", lazy="raise")
    artifacts: Mapped[list[ResultArtifact]] = relationship(
        back_populates="run",
        foreign_keys="ResultArtifact.run_id,ResultArtifact.session_id,ResultArtifact.owner_id",
        lazy="raise",
    )
    tool_executions: Mapped[list[ToolExecution]] = relationship(
        back_populates="run", lazy="raise"
    )


class RunEvent(AgentAppBase):
    """可回放的 SSE 业务事件；心跳不持久化。"""

    __tablename__ = "run_event"
    __table_args__ = (
        Index("idx_event_created", "created_at"),
        CheckConstraint("seq > 0", name="ck_event_seq"),
        CheckConstraint("schema_version > 0", name="ck_event_schema"),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "SSE 业务事件回放日志；心跳不入库",
        },
    )

    run_id: Mapped[str] = mapped_column(
        _ascii_char(36),
        ForeignKey("agent_app.analysis_run.id", name="fk_event_run"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    schema_version: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True), nullable=False, server_default=text("1")
    )
    event_type: Mapped[str] = mapped_column(_ascii_varchar(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    run: Mapped[AnalysisRun] = relationship(back_populates="events", lazy="raise")


class ToolExecution(AgentAppBase):
    """工具调用尝试的审计记录，区分任务恢复次数和工具重试次数。"""

    __tablename__ = "tool_execution"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "task_id", "run_attempt", "attempt", name="uk_tool_attempt"
        ),
        Index("idx_tool_started", "started_at"),
        CheckConstraint("run_attempt > 0 AND attempt > 0", name="ck_tool_attempts"),
        CheckConstraint("cache_hit IN (0, 1)", name="ck_tool_cache"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_tool_status",
        ),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND duration_ms IS NULL) "
            "OR (status <> 'running' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)",
            name="ck_tool_finished",
        ),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "一条工具执行尝试一条审计记录，可分别追踪恢复与工具重试",
        },
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(
        _ascii_char(36),
        ForeignKey("agent_app.analysis_run.id", name="fk_tool_run"),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(_ascii_varchar(64), nullable=False)
    # run_attempt 属于任务恢复，attempt 属于同一工具任务的重试。
    run_attempt: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    attempt: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(_ascii_varchar(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(_ascii_char(64), nullable=False)
    cache_hit: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("0")
    )
    duration_ms: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    status: Mapped[str] = mapped_column(_ascii_varchar(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(_ascii_varchar(64))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    run: Mapped[AnalysisRun] = relationship(
        back_populates="tool_executions", lazy="raise"
    )

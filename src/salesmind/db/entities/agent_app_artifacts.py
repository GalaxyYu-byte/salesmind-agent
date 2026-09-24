"""Persisted analysis results."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, SMALLINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from salesmind.db.base import AgentAppBase
from salesmind.db.entities.agent_app_common import (
    _ascii_char,
    _ascii_varchar,
    _created_at,
)

if TYPE_CHECKING:
    from salesmind.db.entities.agent_app_runs import AnalysisRun


class ResultArtifact(AgentAppBase):
    """持久化的数据集、图表或结构化答案，带归属与版本快照。"""

    __tablename__ = "result_artifact"
    __table_args__ = (
        UniqueConstraint("run_id", "artifact_key", name="uk_artifact_run_key"),
        Index("idx_artifact_run_owner_fk", "run_id", "session_id", "owner_id"),
        Index("idx_artifact_session_created", "session_id", "created_at", "id"),
        Index("idx_artifact_expires", "expires_at"),
        ForeignKeyConstraint(
            ["run_id", "session_id", "owner_id"],
            [
                "agent_app.analysis_run.id",
                "agent_app.analysis_run.session_id",
                "agent_app.analysis_run.owner_id",
            ],
            name="fk_artifact_run_owner",
        ),
        CheckConstraint(
            "kind IN ('DATASET', 'FIGURE', 'ANSWER')", name="ck_artifact_kind"
        ),
        CheckConstraint("schema_version > 0", name="ck_artifact_schema"),
        CheckConstraint("permission_version > 0", name="ck_artifact_permission"),
        CheckConstraint(
            "(data_version IS NOT NULL AND data_version > 0) OR (kind = 'ANSWER' AND data_version IS NULL)",
            name="ck_artifact_data_version",
        ),
        CheckConstraint(
            "(kind = 'DATASET' AND row_count IS NOT NULL) OR (kind <> 'DATASET' AND row_count IS NULL)",
            name="ck_artifact_rows",
        ),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 1048576", name="ck_artifact_bytes"
        ),
        CheckConstraint("expires_at > created_at", name="ck_artifact_expiry"),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "带归属、权限与数据版本的分析结果，默认保留七天",
        },
    )

    id: Mapped[str] = mapped_column(_ascii_char(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    session_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    # 同一任务内的键用于节点重跑时定位已保存的产物。
    artifact_key: Mapped[str] = mapped_column(_ascii_varchar(128), nullable=False)
    kind: Mapped[str] = mapped_column(_ascii_varchar(16), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True), nullable=False, server_default=text("1")
    )
    schema_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    # 读取或复用前需校验权限、数据版本、指标版本和有效期。
    scope_hash: Mapped[str] = mapped_column(_ascii_char(64), nullable=False)
    permission_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False
    )
    data_version: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    metric_version: Mapped[str] = mapped_column(_ascii_varchar(32), nullable=False)
    row_count: Mapped[int | None] = mapped_column(INTEGER(unsigned=True))
    # 数据库限制单条不超过 1 MiB；写入服务还需检查单任务总量。
    byte_size: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    run: Mapped[AnalysisRun] = relationship(
        back_populates="artifacts",
        foreign_keys=[run_id, session_id, owner_id],
        lazy="raise",
    )

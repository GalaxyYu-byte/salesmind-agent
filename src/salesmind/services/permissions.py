"""服务端数据授权；空集合表示无权限，None 表示该维度不额外限制。"""

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from salesmind.db.entities import (
    AppUser,
    SalesOrder,
    SalesRegion,
    SalesRep,
    UserRegionScope,
)

METRICS = frozenset(
    {
        "sales_amount",
        "order_count",
        "quantity",
        "gross_profit",
        "gross_margin",
        "avg_order_value",
        "product_share",
        "growth_rate",
        "sales_cost",
    }
)
ANALYSIS_METRICS = {
    "amount": "sales_amount",
    "count": "order_count",
    "profit": "gross_profit",
    "average": "avg_order_value",
}


@dataclass(frozen=True)
class DataScope:
    user_id: str
    allowed_region_ids: tuple[int, ...] | None
    allowed_rep_ids: tuple[int, ...] | None
    allowed_metrics: tuple[str, ...]
    permission_version: int

    @property
    def scope_hash(self) -> str:
        record = {
            "user_id": self.user_id,
            "allowed_region_ids": sorted(set(self.allowed_region_ids))
            if self.allowed_region_ids is not None
            else None,
            "allowed_rep_ids": sorted(set(self.allowed_rep_ids))
            if self.allowed_rep_ids is not None
            else None,
            "allowed_metrics": sorted(set(self.allowed_metrics)),
            "permission_version": self.permission_version,
        }
        return hashlib.sha256(
            json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    @property
    def scope_label(self) -> str:
        if self.allowed_rep_ids is not None:
            return "本人订单"
        if self.allowed_region_ids is not None:
            return "授权区域"
        return "全企业"

    def require_data(self) -> None:
        if not self.user_id or self.permission_version < 1:
            raise HTTPException(403, "账号权限配置无效，请联系管理员")
        if self.allowed_region_ids == () or self.allowed_rep_ids == ():
            raise HTTPException(403, "账号尚未获得数据范围授权")
        if not self.allowed_metrics:
            raise HTTPException(403, "账号尚未获得指标授权")
        if not set(self.allowed_metrics) <= METRICS:
            raise HTTPException(403, "账号指标权限配置无效，请联系管理员")

    def require_metric(self, metric: str) -> None:
        self.require_data()
        name = ANALYSIS_METRICS.get(metric, metric)
        required = {name}
        if name == "avg_order_value":
            required |= {"sales_amount", "order_count"}
        if name == "gross_margin":
            required |= {"sales_amount", "gross_profit"}
        if not required <= set(self.allowed_metrics):
            raise HTTPException(403, "当前账号无权查看所请求的指标")

    def require_analysis(self, spec) -> None:
        self.require_metric(spec.metric)
        if spec.comparison in {"yoy", "mom"} or spec.goal == "anomalies":
            self.require_metric("growth_rate")
        if (
            spec.scope.region
            and self.allowed_region_ids is not None
            and spec.scope.region not in self.allowed_region_ids
        ):
            raise HTTPException(403, "所请求的区域不在账号授权范围内")

    def order_conditions(self) -> list:
        conditions = []
        if self.allowed_region_ids is not None:
            conditions.append(SalesOrder.region_id.in_(self.allowed_region_ids))
        if self.allowed_rep_ids is not None:
            conditions.append(SalesOrder.rep_id.in_(self.allowed_rep_ids))
        return conditions

    def region_conditions(self) -> list:
        conditions = []
        if self.allowed_region_ids is not None:
            conditions.append(SalesRegion.id.in_(self.allowed_region_ids))
        if self.allowed_rep_ids is not None:
            conditions.append(
                SalesRegion.id.in_(
                    select(SalesOrder.region_id).where(*self.order_conditions())
                )
            )
        return conditions

    def public(self) -> dict:
        return {
            "user_id": self.user_id,
            "permission_version": self.permission_version,
            "scope_hash": self.scope_hash,
            "allowed_metrics": list(self.allowed_metrics),
            "scope_label": self.scope_label,
        }


async def load_data_scope(
    app_session: AsyncSession,
    sales_session: AsyncSession,
    user: AppUser,
) -> DataScope:
    """从可信账号加载授权，不接受请求或模型携带的角色和授权条件。"""
    if user is None or not user.active or user.permission_version < 1:
        raise HTTPException(403, "账号已停用或权限配置无效")
    metrics = user.allowed_metrics_json
    if not isinstance(metrics, list) or any(
        not isinstance(item, str) or item not in METRICS for item in metrics
    ):
        raise HTTPException(403, "账号指标权限配置无效，请联系管理员")
    regions = reps = None
    if user.role == "SALES_REP":
        if not user.rep_id:
            raise HTTPException(403, "销售员账号未绑定有效人员")
        rep = await sales_session.get(SalesRep, user.rep_id)
        if rep is None or not rep.active:
            raise HTTPException(403, "账号绑定的销售员已停用或不存在")
        reps = (user.rep_id,)
    elif user.role == "SALES_MANAGER":
        granted = list(
            await app_session.scalars(
                select(UserRegionScope.region_id).where(
                    UserRegionScope.user_id == user.id
                )
            )
        )
        # 停用区域保留历史授权；不存在的区域不产生可见数据范围。
        regions = (
            tuple(
                sorted(
                    await sales_session.scalars(
                        select(SalesRegion.id).where(SalesRegion.id.in_(granted))
                    )
                )
            )
            if granted
            else ()
        )
    elif user.role != "SALES_DIRECTOR":
        raise HTTPException(403, "账号角色配置无效，请联系管理员")
    return DataScope(
        user.id, regions, reps, tuple(sorted(set(metrics))), user.permission_version
    )

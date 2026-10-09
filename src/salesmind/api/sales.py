"""按当前账号的数据范围与指标白名单返回销售汇总。"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from salesmind.api.auth import CurrentUser, get_current_user
from salesmind.api.dependencies import AppSession, SalesSession
from salesmind.db.entities import AppUser
from salesmind.services.permissions import load_data_scope
from salesmind.services.sales_overview import (
    OverviewQuery,
    OverviewResponse,
    SalesQueryError,
    query_sales_overview,
)

router = APIRouter(
    prefix="/api/v1/sales",
    tags=["sales"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/overview", response_model=OverviewResponse, response_model_exclude_none=True
)
async def sales_overview(
    request: Request,
    response: Response,
    session: SalesSession,
    app_session: AppSession,
    user: CurrentUser,
    query: Annotated[OverviewQuery, Query()],
) -> OverviewResponse:
    """返回已完成订单的当前期及同比基期汇总，仅执行受约束 SELECT。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(request.app.state.settings.sales_query_timeout):
            scope = await load_data_scope(app_session, session, user)
            cache = getattr(request.app.state, "query_cache", None)
            result = (
                await cache.query(session, query, scope, query_sales_overview)
                if cache is not None
                else await query_sales_overview(session, query, scope)
            )
        # 长查询完成后重新读取授权，撤权后的结果不能继续下发。
        await app_session.rollback()
        await session.rollback()
        current_user = await app_session.get(
            AppUser, scope.user_id, populate_existing=True
        )
        current = await load_data_scope(app_session, session, current_user)
        if current.scope_hash != scope.scope_hash:
            raise HTTPException(409, "权限已变化，请重新查询")
        return result
    except SalesQueryError as error:
        raise HTTPException(error.status_code, str(error)) from None
    except TimeoutError:
        raise HTTPException(504, "销售查询超时，请稍后重试") from None
    except SQLAlchemyError:
        # 不把驱动异常、SQL 参数、主机或账号信息返回给浏览器。
        raise HTTPException(503, "销售数据库暂时不可用，请检查连接后重试") from None

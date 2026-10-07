"""登录后查询销售汇总；首版所有启用账号可访问全部销售数据。"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from salesmind.api.auth import get_current_user
from salesmind.api.dependencies import SalesSession
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


@router.get("/overview", response_model=OverviewResponse)
async def sales_overview(
    request: Request,
    response: Response,
    session: SalesSession,
    query: Annotated[OverviewQuery, Query()],
) -> OverviewResponse:
    """返回已完成订单的当前期及同比基期汇总，仅执行受约束 SELECT。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(request.app.state.settings.sales_query_timeout):
            return await query_sales_overview(session, query)
    except SalesQueryError as error:
        raise HTTPException(error.status_code, str(error)) from None
    except TimeoutError:
        raise HTTPException(504, "销售查询超时，请稍后重试") from None
    except SQLAlchemyError:
        # 不把驱动异常、SQL 参数、主机或账号信息返回给浏览器。
        raise HTTPException(503, "销售数据库暂时不可用，请检查连接后重试") from None

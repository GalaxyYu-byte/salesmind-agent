"""销售查询快照缓存；权限和数据水位参与键，Redis 故障退回数据库。"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from pydantic import ValidationError
from redis.exceptions import RedisError

from salesmind.db.entities import DatasetVersion
from salesmind.services.permissions import DataScope
from salesmind.services.sales_overview import (
    OverviewQuery,
    OverviewResponse,
    QueryCacheInfo,
    SalesQueryError,
    resolve_months,
)


class SalesQueryCache:
    def __init__(
        self,
        redis,
        *,
        ttl_seconds=60,
        max_bytes=5000000,
        namespace="default",
        io_timeout=0.5,
    ):
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        self.io_timeout = io_timeout
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()
        self._locks: dict[str, list] = {}

    @asynccontextmanager
    async def _key_lock(self, key):
        entry = self._locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                self._locks.pop(key, None)

    async def query(
        self,
        session,
        query: OverviewQuery,
        scope: DataScope,
        loader: Callable[..., Awaitable[OverviewResponse]],
    ) -> OverviewResponse:
        # 缓存不替代授权，每次请求仍读取当前账号范围。
        scope.require_data()
        if (
            query.region_id is not None
            and scope.allowed_region_ids is not None
            and query.region_id not in scope.allowed_region_ids
        ):
            raise SalesQueryError("所请求的区域不在账号授权范围内", 403)
        if self.redis is None or self.ttl_seconds == 0:
            return await loader(session, query, scope)
        # populate_existing 防止同一个 Session 身份映射掩盖已发布的新版本。
        version = await session.get(DatasetVersion, 1, populate_existing=True)
        if version is None or version.complete_through is None:
            raise SalesQueryError("数据库尚未发布完整数据水位", 503)
        months = resolve_months(query, version.available_from, version.complete_through)
        normalized = query.model_copy(
            update={
                "start_month": months[0],
                "end_month": months[-1],
                "baseline_mode": query.baseline_mode
                if query.comparison == "mom"
                else "period",
            }
        )
        stamp = {
            "version": version.version,
            "refreshed_at": str(version.refreshed_at),
            "available_from": str(version.available_from),
            "complete_through": str(version.complete_through),
        }
        content = {
            "query": normalized.model_dump(exclude={"refresh"}),
            "scope": scope.scope_hash,
            "dataset": stamp,
        }
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        key = f"salesmind:query:v1:{self.namespace}:{digest}"
        cache_available = True

        async def cached():
            nonlocal cache_available
            if query.refresh or not cache_available:
                return None
            try:
                async with asyncio.timeout(self.io_timeout):
                    value = await self.redis.get(key)
                if (
                    value is None
                    or len(value.encode() if isinstance(value, str) else value)
                    > self.max_bytes
                ):
                    return None
                result = OverviewResponse.model_validate_json(value)
                if (
                    result.access is None
                    or result.access.scope_hash != scope.scope_hash
                    or result.access.user_id != scope.user_id
                    or result.months != months
                    or result.region_id != query.region_id
                    or result.comparison != query.comparison
                    or result.dataset.version != version.version
                    or str(result.dataset.available_from) != stamp["available_from"]
                    or str(result.dataset.complete_through) != stamp["complete_through"]
                ):
                    return None
                async with asyncio.timeout(self.io_timeout):
                    remaining = await self.redis.ttl(key)
                if remaining <= 0:
                    return None
                return result.model_copy(
                    update={"cache": QueryCacheInfo(hit=True, ttl_seconds=remaining)}
                )
            except (RedisError, OSError, TimeoutError):
                # 本次请求遇到缓存故障后不再重试，让查询预算留给数据库。
                cache_available = False
                return None
            except (ValidationError, ValueError, TypeError):
                return None

        result = await cached()
        if result is not None:
            return result
        # 同进程相同查询合并等待；锁只保护数据获取，不共享 SQLAlchemy Session。
        async with self._key_lock(key):
            result = await cached()
            if result is not None:
                return result
            result = await loader(session, normalized, scope)
            if (
                cache_available
                and result.access is not None
                and result.access.scope_hash == scope.scope_hash
                and result.months == months
                and result.region_id == query.region_id
                and result.comparison == query.comparison
                and result.dataset.version == version.version
                and str(result.dataset.available_from) == stamp["available_from"]
                and str(result.dataset.complete_through) == stamp["complete_through"]
            ):
                payload = result.model_dump_json(exclude={"cache"})
                if len(payload.encode()) <= self.max_bytes:
                    try:
                        async with asyncio.timeout(self.io_timeout):
                            await self.redis.set(key, payload, ex=self.ttl_seconds)
                        result = result.model_copy(
                            update={
                                "cache": QueryCacheInfo(
                                    hit=False, ttl_seconds=self.ttl_seconds
                                )
                            }
                        )
                    except (RedisError, OSError, TimeoutError):
                        pass
            return result

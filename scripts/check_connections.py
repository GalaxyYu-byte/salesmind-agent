"""只读检查 .env 中两个 MySQL schema 与 Redis 的实际连通性。"""

import asyncio
import json
from contextlib import AsyncExitStack

from pydantic import ValidationError

from salesmind.core.config import Settings
from salesmind.db.session import Database
from salesmind.infrastructure.health import check_readiness
from salesmind.infrastructure.redis import RedisStore


async def check() -> int:
    try:
        settings = Settings()
    except ValidationError:
        print(json.dumps({"status": "invalid_configuration"}))
        return 1
    async with AsyncExitStack() as resources:
        database = Database(settings)
        resources.push_async_callback(database.dispose)
        redis = RedisStore(settings)
        resources.push_async_callback(redis.close)
        result = await check_readiness(database, redis, settings.health_check_timeout)
        print(json.dumps(result))
        return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))

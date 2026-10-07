"""Redis 异步客户端与连接池生命周期。"""

from redis.asyncio import Redis

from salesmind.core.config import Settings


class RedisStore:
    """按当前部署接入一台 Redis；Checkpoint 与缓存逻辑后续实现。"""

    def __init__(self, settings: Settings):
        self.client: Redis | None = None
        if settings.redis_url is not None:
            self.client = Redis.from_url(
                settings.redis_url.get_secret_value(),
                decode_responses=True,
                max_connections=settings.redis_max_connections,
                socket_connect_timeout=settings.redis_connect_timeout,
                socket_timeout=settings.redis_socket_timeout,
                health_check_interval=30,
            )

    async def close(self) -> None:
        """关闭客户端及其持有的连接池。"""
        if self.client is not None:
            await self.client.aclose()

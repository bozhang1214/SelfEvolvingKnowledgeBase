"""L2 中期记忆：基于 Redis 的跨会话偏好与近期话题。

- 偏好：每用户一个 Redis Hash，字段=偏好键，值=JSON（value/confidence/ts）
- 近期话题：每用户一个 Redis List（LPUSH 新话题在前，LTRIM 截断，LRU）
- 均带 TTL 过期（默认 30 天），Redis 不可用时调用方自行降级（本类不吞异常）

使用方式：
    from app.memory.session_memory import RedisSessionMemory

    l2 = RedisSessionMemory("redis://localhost:6379/0", max_items=50, ttl_days=30)
    await l2.record_topic("u1", "Python 装饰器", conv_id="c1")
    topics = await l2.get_recent_topics("u1")
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.logging import get_logger
from app.memory.base import SessionMemoryBackend

logger = get_logger(__name__)


class RedisSessionMemory(SessionMemoryBackend):
    """基于 Redis 的 L2 中期记忆实现（跨会话偏好 + 近期话题）。"""

    def __init__(
        self,
        redis_url: str,
        max_items: int = 50,
        ttl_days: int = 30,
        key_prefix: str = "sekb:l2:",
        redis_client: Any = None,
    ) -> None:
        """
        Args:
            redis_url: Redis 连接串（如 redis://localhost:6379/0）
            max_items: 每用户最多保留的近期话题数（LRU 截断）
            ttl_days: 偏好/话题的过期天数
            key_prefix: Redis key 前缀
            redis_client: 注入的 redis 客户端（测试用）；None 则懒加载真实连接
        """
        self.redis_url = redis_url
        self.max_items = max_items
        self.ttl_seconds = ttl_days * 86400
        self.prefix = key_prefix
        self._redis: Any = redis_client

    def _get_redis(self) -> Any:
        """懒加载 redis.asyncio 客户端。"""
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def ping(self) -> bool:
        """连接健康检查。"""
        try:
            await self._get_redis().ping()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis L2 连接失败", error=str(e))
            return False

    # ============================================================
    # 偏好
    # ============================================================

    async def upsert_preference(
        self, user_id: str, key: str, value: Any, confidence: float
    ) -> None:
        """写入/更新一条用户偏好（Hash + TTL）。"""
        redis = self._get_redis()
        hkey = f"{self.prefix}pref:{user_id}"
        payload = json.dumps(
            {"value": value, "confidence": confidence, "ts": time.time()},
            ensure_ascii=False,
        )
        await redis.hset(hkey, key, payload)
        await redis.expire(hkey, self.ttl_seconds)

    async def get_preferences(self, user_id: str) -> list[dict[str, Any]]:
        """读取某用户全部偏好。"""
        redis = self._get_redis()
        hkey = f"{self.prefix}pref:{user_id}"
        raw = await redis.hgetall(hkey)
        out: list[dict[str, Any]] = []
        for k, v in raw.items():
            try:
                data = json.loads(v)
                out.append({"key": k, **data})
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    # ============================================================
    # 近期话题
    # ============================================================

    async def record_topic(self, user_id: str, topic: str, conv_id: str = "") -> None:
        """记录一个近期话题（LPUSH 到队首 + LTRIM 截断 + TTL）。"""
        if not topic:
            return
        redis = self._get_redis()
        lkey = f"{self.prefix}topics:{user_id}"
        payload = json.dumps(
            {"topic": topic, "conv_id": conv_id, "ts": time.time()},
            ensure_ascii=False,
        )
        await redis.lpush(lkey, payload)
        await redis.ltrim(lkey, 0, self.max_items - 1)
        await redis.expire(lkey, self.ttl_seconds)

    async def get_recent_topics(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """读取某用户最近的话题（新在前）。"""
        redis = self._get_redis()
        lkey = f"{self.prefix}topics:{user_id}"
        raw = await redis.lrange(lkey, 0, max(0, limit - 1))
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    async def close(self) -> None:
        """释放连接（应用关闭时调用）。"""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis L2 关闭失败", error=str(e))
            finally:
                self._redis = None

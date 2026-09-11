"""对话 token / 费用统计服务（Redis 存储）。

按「对话」与「用户」两个维度累计 token 与费用（USD），供前端展示：
- 单对话：该对话累计 token 与约合人民币费用
- 用户累计：所有对话累计 token 与费用

Redis 结构（Hash，HINCRBY/HINCRBYFLOAT 原子累加）：
- ``sekb:usage:conv:{conv_id}`` → input_tokens / output_tokens / tokens / calls / cost_usd
- ``sekb:usage:user:{user_id}`` → 同上

Redis 不可用时静默降级（读取返回零值、写入不报错），不影响聊天主流程。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class UsageService:
    """对话/用户维度的 token 与费用累计（Redis）。"""

    def __init__(
        self,
        redis_url: str,
        usd_to_cny: float = 7.2,
        key_prefix: str = "sekb:usage:",
        redis_client: Any = None,
    ) -> None:
        self.redis_url = redis_url
        self.usd_to_cny = usd_to_cny
        self.prefix = key_prefix
        self._redis: Any = redis_client

    def _get_redis(self) -> Any:
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
            logger.warning("用量统计 Redis 连接失败", error=str(e))
            return False

    async def record(
        self,
        conv_id: str,
        user_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """累加一次对话调用的 token 与费用（同时更新对话维度与用户维度）。"""
        if not conv_id or not user_id:
            return
        in_t = int(input_tokens or 0)
        out_t = int(output_tokens or 0)
        total = in_t + out_t
        cost = float(cost_usd or 0.0)
        redis = self._get_redis()
        try:
            for key in (f"{self.prefix}conv:{conv_id}", f"{self.prefix}user:{user_id}"):
                await redis.hincrby(key, "input_tokens", in_t)
                await redis.hincrby(key, "output_tokens", out_t)
                await redis.hincrby(key, "tokens", total)
                await redis.hincrby(key, "calls", 1)
                await redis.hincrbyfloat(key, "cost_usd", cost)
        except Exception as e:  # noqa: BLE001 - 统计失败不影响聊天
            logger.warning("用量统计写入失败", error=str(e))

    async def _read(self, key: str) -> dict[str, Any]:
        try:
            return await self._get_redis().hgetall(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("用量统计读取失败", error=str(e))
            return {}

    def _format(self, raw: dict[str, Any]) -> dict[str, Any]:
        """把 Redis Hash 原始值格式化为带 CNY 的统计结构。"""
        def _num(field: str) -> float:
            try:
                return float(raw.get(field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        input_tokens = int(_num("input_tokens"))
        output_tokens = int(_num("output_tokens"))
        tokens = int(_num("tokens")) or (input_tokens + output_tokens)
        cost_usd = round(_num("cost_usd"), 6)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens": tokens,
            "calls": int(_num("calls")),
            "cost_usd": cost_usd,
            "cost_cny": round(cost_usd * self.usd_to_cny, 4),
        }

    async def get_conversation(self, conv_id: str) -> dict[str, Any]:
        """某对话的累计统计。"""
        if not conv_id:
            return self._format({})
        return self._format(await self._read(f"{self.prefix}conv:{conv_id}"))

    async def get_user_total(self, user_id: str) -> dict[str, Any]:
        """某用户的全部对话累计统计。"""
        if not user_id:
            return self._format({})
        return self._format(await self._read(f"{self.prefix}user:{user_id}"))

    async def close(self) -> None:
        """释放 Redis 连接。"""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as e:  # noqa: BLE001
                logger.warning("用量统计 Redis 关闭失败", error=str(e))
            finally:
                self._redis = None

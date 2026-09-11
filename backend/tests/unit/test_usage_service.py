"""对话用量统计服务（UsageService）的单元测试。"""

from __future__ import annotations

import pytest

from app.services.usage_service import UsageService


class _FakeRedis:
    """内存版 Redis（实现 UsageService 用到的 Hash 命令）。"""

    def __init__(self):
        self.hashes: dict[str, dict[str, float]] = {}
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        h = self.hashes.setdefault(key, {})
        h[field] = h.get(field, 0) + amount
        return int(h[field])

    async def hincrbyfloat(self, key: str, field: str, amount: float) -> float:
        h = self.hashes.setdefault(key, {})
        h[field] = h.get(field, 0.0) + amount
        return float(h[field])

    async def hgetall(self, key: str) -> dict[str, float]:
        return {k: str(v) for k, v in self.hashes.get(key, {}).items()}

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def service(fake_redis):
    return UsageService("redis://x", usd_to_cny=7.2, redis_client=fake_redis)


class TestRecord:
    async def test_record_accumulates_tokens_and_cost(self, service):
        await service.record("c1", "u1", input_tokens=100, output_tokens=50, cost_usd=0.001)
        await service.record("c1", "u1", input_tokens=200, output_tokens=80, cost_usd=0.002)

        conv = await service.get_conversation("c1")
        assert conv["input_tokens"] == 300
        assert conv["output_tokens"] == 130
        assert conv["tokens"] == 430
        assert conv["calls"] == 2
        assert conv["cost_usd"] == pytest.approx(0.003)

        total = await service.get_user_total("u1")
        assert total["tokens"] == 430
        assert total["calls"] == 2

    async def test_cost_cny_uses_fixed_rate(self, service):
        await service.record("c1", "u1", 0, 0, cost_usd=1.0)
        conv = await service.get_conversation("c1")
        assert conv["cost_cny"] == pytest.approx(7.2)

    async def test_user_total_isolated_per_user(self, service):
        await service.record("c1", "u1", 100, 0, 0.001)
        await service.record("c2", "u2", 200, 0, 0.002)
        assert (await service.get_user_total("u1"))["tokens"] == 100
        assert (await service.get_user_total("u2"))["tokens"] == 200

    async def test_conversation_isolated(self, service):
        await service.record("c1", "u1", 100, 0, 0.001)
        await service.record("c2", "u1", 200, 0, 0.002)
        assert (await service.get_conversation("c1"))["tokens"] == 100
        assert (await service.get_conversation("c2"))["tokens"] == 200
        # 用户累计为两者之和
        assert (await service.get_user_total("u1"))["tokens"] == 300

    async def test_missing_ids_noop(self, service):
        await service.record("", "u1", 100, 0, 0.001)
        await service.record("c1", "", 100, 0, 0.001)
        assert (await service.get_user_total("u1"))["tokens"] == 0


class TestReadEmpty:
    async def test_empty_conversation_returns_zeros(self, service):
        conv = await service.get_conversation("nope")
        assert conv == {
            "input_tokens": 0, "output_tokens": 0, "tokens": 0,
            "calls": 0, "cost_usd": 0.0, "cost_cny": 0.0,
        }

    async def test_empty_user_returns_zeros(self, service):
        assert (await service.get_user_total("nobody"))["tokens"] == 0


class TestLifecycle:
    async def test_ping(self, service):
        assert await service.ping() is True

    async def test_close(self, service, fake_redis):
        await service.close()
        assert fake_redis.closed is True


class TestRedisFailure:
    async def test_record_failure_is_silent(self):
        class _Broken:
            async def hincrby(self, *a, **k):
                raise RuntimeError("redis down")

            async def hincrbyfloat(self, *a, **k):
                raise RuntimeError("redis down")

            async def hgetall(self, *a, **k):
                raise RuntimeError("redis down")

        svc = UsageService("redis://x", redis_client=_Broken())
        # 不应抛异常
        await svc.record("c1", "u1", 10, 5, 0.001)
        assert (await svc.get_conversation("c1"))["tokens"] == 0

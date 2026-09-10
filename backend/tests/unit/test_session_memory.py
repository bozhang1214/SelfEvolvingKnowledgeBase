"""Redis L2 中期记忆（RedisSessionMemory）的单元测试。"""

from __future__ import annotations

import pytest

from app.memory.session_memory import RedisSessionMemory


class _FakeRedis:
    """内存版 Redis 客户端（实现 RedisSessionMemory 用到的命令）。"""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        self.lists[key] = self.lists.get(key, [])[start : stop + 1]
        return True

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        return self.lists.get(key, [])[start : stop + 1]

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def memory(fake_redis):
    return RedisSessionMemory("redis://x", max_items=3, ttl_days=1, redis_client=fake_redis)


class TestPreferences:
    async def test_upsert_and_get_preferences(self, memory):
        await memory.upsert_preference("u1", "language", "中文", 0.9)
        await memory.upsert_preference("u1", "style", "简洁", 0.7)
        prefs = await memory.get_preferences("u1")
        keys = {p["key"] for p in prefs}
        assert keys == {"language", "style"}
        lang = next(p for p in prefs if p["key"] == "language")
        assert lang["value"] == "中文"
        assert lang["confidence"] == pytest.approx(0.9)

    async def test_empty_preferences(self, memory):
        assert await memory.get_preferences("u2") == []

    async def test_upsert_overwrites_same_key(self, memory):
        await memory.upsert_preference("u1", "style", "简洁", 0.7)
        await memory.upsert_preference("u1", "style", "详细", 0.8)
        prefs = await memory.get_preferences("u1")
        assert len(prefs) == 1
        assert prefs[0]["value"] == "详细"


class TestRecentTopics:
    async def test_record_and_get_topics(self, memory):
        await memory.record_topic("u1", "Python 装饰器", conv_id="c1")
        await memory.record_topic("u1", "asyncio", conv_id="c2")
        topics = await memory.get_recent_topics("u1")
        # 新话题在前
        assert [t["topic"] for t in topics] == ["asyncio", "Python 装饰器"]

    async def test_topics_trimmed_to_max_items(self, memory):
        for i in range(5):
            await memory.record_topic("u1", f"topic-{i}")
        topics = await memory.get_recent_topics("u1", limit=10)
        # max_items=3，只保留最近 3 条
        assert len(topics) == 3
        assert topics[0]["topic"] == "topic-4"

    async def test_empty_topic_skipped(self, memory):
        await memory.record_topic("u1", "")
        assert await memory.get_recent_topics("u1") == []

    async def test_empty_topics(self, memory):
        assert await memory.get_recent_topics("u2") == []


class TestLifecycle:
    async def test_ping_true(self, memory):
        assert await memory.ping() is True

    async def test_close_calls_aclose(self, memory, fake_redis):
        await memory.close()
        assert fake_redis.closed is True

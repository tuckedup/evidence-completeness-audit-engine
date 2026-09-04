from __future__ import annotations

import asyncio
import sys

import pytest

from ecsae.cache import MemoryCache, TieredCache, encode_json


def test_memory_and_tiered_cache_paths() -> None:
    async def exercise() -> None:
        cache = MemoryCache(max_entries=1, ttl_seconds=60)
        assert await cache.get("missing") is None
        await cache.set("a", b"1")
        assert await cache.get("a") == b"1"
        await cache.set("b", b"2")
        assert await cache.get("a") is None
        async with cache.lock("b"):
            assert await cache.get("b") == b"2"
        tiered = TieredCache(2, 60)
        await tiered.connect()
        await tiered.set("x", b"3")
        assert await tiered.get("x") == b"3"
        async with tiered.lock("x"):
            pass
        await tiered.close()
    asyncio.run(exercise())
    assert encode_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_cache_expiry_redis_success_and_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRedis:
        def __init__(self):
            self.values = {"remote": b"r"}
            self.closed = False
            self.fail = False

        async def ping(self):
            return True

        async def get(self, key):
            if self.fail:
                raise ConnectionError
            return self.values.get(key)

        async def set(self, key, payload, ex):
            if self.fail:
                raise ConnectionError
            self.values[key] = payload

        async def aclose(self):
            self.closed = True

    fake = FakeRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *args, **kwargs: fake)

    async def exercise() -> None:
        memory = MemoryCache(2, 1)
        await memory.set("expired", b"x")
        memory._values["expired"] = (0.0, b"x")
        assert await memory.get("expired") is None
        cache = TieredCache(2, 60, "redis://test")
        await cache.connect()
        assert cache.redis is fake
        assert await cache.get("remote") == b"r"
        assert await cache.memory.get("remote") == b"r"
        await cache.set("written", b"w")
        assert fake.values["written"] == b"w"
        fake.fail = True
        assert await cache.get("absent") is None
        await cache.set("still-local", b"local")
        assert await cache.memory.get("still-local") == b"local"
        await cache.close()
        assert fake.closed

    asyncio.run(exercise())

    def broken(*args, **kwargs):
        raise ConnectionError

    monkeypatch.setattr("redis.asyncio.from_url", broken)

    async def connect_failure() -> None:
        cache = TieredCache(1, 1, "redis://broken")
        await cache.connect()
        assert cache.redis is None

    asyncio.run(connect_failure())


def test_encode_json_stdlib_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "orjson", None)
    assert encode_json({"é": 1, "a": 2}) == '{"a":2,"é":1}'.encode()

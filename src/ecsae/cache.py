from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class MemoryCache:
    def __init__(self, max_entries: int, ttl_seconds: int):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._values: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._key_locks = [asyncio.Lock() for _ in range(64)]

    async def get(self, key: str) -> bytes | None:
        now = time.monotonic()
        item = self._values.get(key)
        if item is None:
            return None
        expires, payload = item
        if expires < now:
            del self._values[key]
            return None
        self._values.move_to_end(key)
        return payload

    async def set(self, key: str, payload: bytes) -> None:
        self._values[key] = (time.monotonic() + self.ttl_seconds, payload)
        self._values.move_to_end(key)
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)

    @asynccontextmanager
    async def lock(self, key: str) -> AsyncIterator[None]:
        stripe = hashlib.blake2b(key.encode(), digest_size=1).digest()[0] % len(self._key_locks)
        lock = self._key_locks[stripe]
        async with lock:
            yield


class TieredCache:
    """In-process LRU backed by optional Redis.

    Redis failures degrade to the memory cache; audit availability does not depend on a
    cache. A per-process key lock prevents local cache stampedes.
    """

    def __init__(self, max_entries: int, ttl_seconds: int, redis_url: str | None = None):
        self.memory = MemoryCache(max_entries, ttl_seconds)
        self.ttl_seconds = ttl_seconds
        self.redis_url = redis_url
        self.redis: Any = None

    async def connect(self) -> None:
        if not self.redis_url:
            return
        try:
            from redis.asyncio import from_url

            client = from_url(self.redis_url, encoding=None, decode_responses=False)
            await client.ping()
            self.redis = client
        except Exception:
            self.redis = None

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()

    async def get(self, key: str) -> bytes | None:
        payload = await self.memory.get(key)
        if payload is not None:
            return payload
        if self.redis is not None:
            try:
                payload = await self.redis.get(key)
                if payload is not None:
                    await self.memory.set(key, payload)
                    return payload
            except Exception:
                pass
        return None

    async def set(self, key: str, payload: bytes) -> None:
        await self.memory.set(key, payload)
        if self.redis is not None:
            try:
                await self.redis.set(key, payload, ex=self.ttl_seconds)
            except Exception:
                pass

    @asynccontextmanager
    async def lock(self, key: str) -> AsyncIterator[None]:
        # The local lock is sufficient for deterministic duplicate computation. Redis SETNX
        # is intentionally omitted: duplicate pure audits are safer than a distributed lock
        # that can strand requests after worker termination.
        async with self.memory.lock(key):
            yield


def encode_json(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    try:
        import orjson

        return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
    except ImportError:  # minimal core installs retain deterministic serialization
        import json

        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

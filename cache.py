"""
Content-addressable in-memory cache for LLM responses.

Uses a SHA-256 hash of the serialized request body as the cache key.
This means identical reconciliation requests return cached results
without hitting the Anthropic API again, reducing cost and latency.

In production, this would be backed by Redis or Memcached.
"""

import hashlib
import json
import time
from typing import Any, Optional


class ResponseCache:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 500):
        self._store: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_seconds
        self._max_entries = max_entries

    @staticmethod
    def _compute_key(data: dict) -> str:
        """Generate a deterministic hash from request data."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def get(self, request_data: dict) -> Optional[dict]:
        """Retrieve a cached response if it exists and hasn't expired."""
        key = self._compute_key(request_data)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() - entry["cached_at"] > self._ttl:
            del self._store[key]
            return None
        return entry["response"]

    def set(self, request_data: dict, response: dict) -> None:
        """Store a response in the cache."""
        if len(self._store) >= self._max_entries:
            self._evict_oldest()
        key = self._compute_key(request_data)
        self._store[key] = {
            "response": response,
            "cached_at": time.time(),
        }

    def _evict_oldest(self) -> None:
        """Remove the oldest cache entry when capacity is reached."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k]["cached_at"])
        del self._store[oldest_key]

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


# Module-level singleton
reconciliation_cache = ResponseCache()
quality_cache = ResponseCache()

"""In-process LRU + TTL cache of validated uploads, keyed by an opaque image_id.

Lets the client upload once and re-trace many times while tuning parameters,
instead of re-sending the file on every slider tick. Per process: with more
than one worker the client must be ready to re-upload on `image_expired`.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from studi0trace.engines.base import TraceInput


@dataclass
class _Entry:
    image: TraceInput
    size: int
    expires_at: float


class UploadCache:
    def __init__(self, max_bytes: int, ttl_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[str, _Entry] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def size_of(image: TraceInput) -> int:
        return len(image.source_bytes) + image.width * image.height * 4

    def put(self, image: TraceInput) -> str:
        image_id = uuid.uuid4().hex
        entry = _Entry(image=image, size=self.size_of(image), expires_at=self._clock() + self.ttl_seconds)
        with self._lock:
            self._purge_expired()
            while self._items and self._bytes + entry.size > self.max_bytes:
                _, evicted = self._items.popitem(last=False)
                self._bytes -= evicted.size
            self._items[image_id] = entry
            self._bytes += entry.size
        return image_id

    def get(self, image_id: str) -> TraceInput | None:
        with self._lock:
            entry = self._items.get(image_id)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                self._items.pop(image_id)
                self._bytes -= entry.size
                return None
            self._items.move_to_end(image_id)
            entry.expires_at = self._clock() + self.ttl_seconds  # sliding TTL: active sessions stay warm
            return entry.image

    def _purge_expired(self) -> None:
        now = self._clock()
        for key in [k for k, e in self._items.items() if e.expires_at <= now]:
            self._bytes -= self._items.pop(key).size

    def __len__(self) -> int:
        return len(self._items)

    @property
    def bytes(self) -> int:
        return self._bytes

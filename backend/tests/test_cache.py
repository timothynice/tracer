from studi0trace.imaging.cache import UploadCache
from studi0trace.imaging.intake import load_upload
from tests.conftest import make_png

LIMITS = dict(max_bytes=1 << 30, max_pixels=1 << 30)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def image(size: int = 8):
    return load_upload(make_png(size, size), **LIMITS)


def test_roundtrip_and_size_accounting():
    cache = UploadCache(max_bytes=1 << 20, ttl_seconds=60)
    img = image()
    image_id = cache.put(img)
    assert len(image_id) == 32
    assert cache.get(image_id) is img
    assert cache.bytes == UploadCache.size_of(img)
    assert cache.get("nope") is None


def test_lru_eviction_respects_budget():
    one = UploadCache.size_of(image())
    cache = UploadCache(max_bytes=one * 2 + 1, ttl_seconds=60)
    a, b = cache.put(image()), cache.put(image())
    assert cache.get(a) is not None  # touch a → b is now least recent
    c = cache.put(image())
    assert len(cache) == 2 and cache.bytes <= cache.max_bytes
    assert cache.get(b) is None
    assert cache.get(a) is not None and cache.get(c) is not None


def test_ttl_expiry_is_sliding():
    clock = Clock()
    cache = UploadCache(max_bytes=1 << 20, ttl_seconds=10, clock=clock)
    image_id = cache.put(image())
    clock.t += 8
    assert cache.get(image_id) is not None  # touched at t+8 → expires t+18
    clock.t += 8
    assert cache.get(image_id) is not None  # t+16 < t+18
    clock.t += 11
    assert cache.get(image_id) is None
    assert cache.bytes == 0


def test_expired_entries_are_purged_on_put():
    clock = Clock()
    cache = UploadCache(max_bytes=1 << 20, ttl_seconds=10, clock=clock)
    cache.put(image())
    clock.t += 11
    cache.put(image())
    assert len(cache) == 1

import threading
import time


class TTLCache:
    """Tiny thread-safe TTL cache. Cleared on every panel mutation."""

    def __init__(self):
        self._d = {}
        self._l = threading.Lock()

    def get(self, key):
        with self._l:
            v = self._d.get(key)
            if v is None:
                return None
            if v[0] < time.time():
                self._d.pop(key, None)
                return None
            return v[1]

    def set(self, key, value, ttl):
        with self._l:
            if len(self._d) > 2000:
                now = time.time()
                self._d = {k: v for k, v in self._d.items() if v[0] > now}
            self._d[key] = (time.time() + ttl, value)

    def clear(self):
        with self._l:
            self._d.clear()


cache = TTLCache()


def cached(ttl):
    """Decorator: cache a function result for `ttl` seconds (args must be hashable)."""

    def deco(fn):
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__name__, args, tuple(sorted(kwargs.items())))
            hit = cache.get(key)
            if hit is not None:
                return hit[0]
            val = fn(*args, **kwargs)
            cache.set(key, (val,), ttl)
            return val

        wrapper.__name__ = fn.__name__
        return wrapper

    return deco

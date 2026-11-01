"""Per-user flood protection with escalating mutes."""
import threading
import time
from collections import deque


class AntiSpam:
    def __init__(self, window=5, limit=7, base_mute=30, max_mute=900):
        self.window, self.limit, self.base_mute, self.max_mute = window, limit, base_mute, max_mute
        self._s = {}
        self._l = threading.Lock()
        self._gc_at = time.time()

    def check(self, uid):
        """Returns (allowed, should_warn_user, seconds_left)."""
        t = time.time()
        with self._l:
            st = self._s.setdefault(uid, {"hits": deque(), "mute": 0.0, "strikes": 0, "last": 0.0})
            if st["mute"] > t:
                return False, False, int(st["mute"] - t) + 1
            hits = st["hits"]
            hits.append(t)
            while hits and hits[0] < t - self.window:
                hits.popleft()
            if len(hits) > self.limit:
                if t - st["last"] > 3600:
                    st["strikes"] = 0
                st["strikes"] += 1
                st["last"] = t
                dur = min(self.base_mute * 2 ** (st["strikes"] - 1), self.max_mute)
                st["mute"] = t + dur
                hits.clear()
                return False, True, int(dur)
            if t - self._gc_at > 600:
                self._gc(t)
            return True, False, 0

    def _gc(self, t):
        self._gc_at = t
        for k in [k for k, s in self._s.items() if s["mute"] < t and (not s["hits"] or s["hits"][-1] < t - 3600)]:
            self._s.pop(k, None)


spam = AntiSpam()

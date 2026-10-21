"""Thin database layer over Firebase Realtime Database.

Nothing in the application deletes records: "deletes" are soft (deleted=True).
A MemoryBackend is included for local tests (USE_MEMORY_DB=1).
"""
import copy
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from config import Config

log = logging.getLogger("db")
backend = None
_pool = ThreadPoolExecutor(max_workers=8)


# ----------------------------------------------------------------- memory backend
def _strip_none(v):
    if isinstance(v, dict):
        return {k: _strip_none(x) for k, x in v.items() if x is not None}
    return v


class MemoryBackend:
    def __init__(self):
        self.data = {}
        self.lock = threading.RLock()

    @staticmethod
    def _parts(path):
        return [p for p in str(path).strip("/").split("/") if p]

    def get(self, path):
        with self.lock:
            node = self.data
            for p in self._parts(path):
                if not isinstance(node, dict) or p not in node:
                    return None
                node = node[p]
            return copy.deepcopy(node)

    def set(self, path, value):
        with self.lock:
            parts = self._parts(path)
            if not parts:
                self.data = _strip_none(copy.deepcopy(value)) or {}
                return
            node = self.data
            for p in parts[:-1]:
                if not isinstance(node.get(p), dict):
                    node[p] = {}
                node = node[p]
            if value is None:
                node.pop(parts[-1], None)
            else:
                node[parts[-1]] = _strip_none(copy.deepcopy(value))

    def update(self, path, values):
        with self.lock:
            for k, v in values.items():
                self.set(str(path).rstrip("/") + "/" + k, v)

    def transaction(self, path, fn):
        with self.lock:
            new = fn(self.get(path))
            self.set(path, new)
            return new

    def _children(self, path):
        node = self.get(path)
        return node if isinstance(node, dict) else {}

    def query_equal(self, path, child, value):
        return {k: v for k, v in self._children(path).items() if isinstance(v, dict) and v.get(child) == value}

    def query_range(self, path, child, start, end):
        out = {}
        for k, v in self._children(path).items():
            if not isinstance(v, dict) or v.get(child) is None:
                continue
            x = v[child]
            if (start is None or x >= start) and (end is None or x <= end):
                out[k] = v
        return dict(sorted(out.items(), key=lambda kv: kv[1][child]))

    def query_last(self, path, child, n):
        items = [(k, v) for k, v in self._children(path).items() if isinstance(v, dict)]
        items.sort(key=lambda kv: kv[1].get(child) or 0)
        return dict(items[-n:])

    def shallow(self, path):
        return list(self._children(path).keys())


# ----------------------------------------------------------------- firebase backend
class FirebaseBackend:
    def __init__(self):
        import firebase_admin
        from firebase_admin import credentials
        from firebase_admin import db as fdb

        if not Config.FIREBASE_DB_URL:
            raise RuntimeError("FIREBASE_DB_URL is not set")
        if Config.FIREBASE_CREDENTIALS_JSON:
            cred = credentials.Certificate(json.loads(Config.FIREBASE_CREDENTIALS_JSON))
        else:
            cred = credentials.Certificate(Config.FIREBASE_CREDENTIALS_FILE)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {"databaseURL": Config.FIREBASE_DB_URL})
        self.fdb = fdb

    def _ref(self, path):
        return self.fdb.reference("/" + str(path).strip("/"))

    def get(self, path):
        return self._ref(path).get()

    def set(self, path, value):
        if value is None:
            self._ref(path).delete()  # only used to clear transient fields (bot state, device binding)
        else:
            self._ref(path).set(value)

    def update(self, path, values):
        self._ref(path).update(values)

    def transaction(self, path, fn):
        return self._ref(path).transaction(fn)

    def query_equal(self, path, child, value):
        return self._ref(path).order_by_child(child).equal_to(value).get() or {}

    def query_range(self, path, child, start, end):
        q = self._ref(path).order_by_child(child)
        if start is not None:
            q = q.start_at(start)
        if end is not None:
            q = q.end_at(end)
        return q.get() or {}

    def query_last(self, path, child, n):
        return self._ref(path).order_by_child(child).limit_to_last(n).get() or {}

    def shallow(self, path):
        res = self._ref(path).get(shallow=True)
        return list(res.keys()) if isinstance(res, dict) else []


# ----------------------------------------------------------------- public helpers
def init():
    global backend
    if backend is not None:
        return
    backend = MemoryBackend() if Config.USE_MEMORY_DB else FirebaseBackend()
    log.info("Database backend: %s", type(backend).__name__)


def get(path, default=None):
    v = backend.get(path)
    return default if v is None else v


def set(path, value):  # noqa: A001
    backend.set(path, value)


def update(path, values):
    backend.update(path, values)


def transaction(path, fn):
    return backend.transaction(path, fn)


def set_if_absent(path, value):
    """Atomically write value only if nothing is stored. Returns True if written."""
    state = {"w": False}

    def fn(cur):
        state["w"] = False
        if cur is None:
            state["w"] = True
            return value
        return cur

    transaction(path, fn)
    return state["w"]


def _with_ids(d):
    out = []
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            v = dict(v)
            v.setdefault("id", k)
            out.append(v)
    return out


def get_list(path):
    return _with_ids(get(path, {}))


def query_equal(path, child, value):
    return _with_ids(backend.query_equal(path, child, value))


def query_range(path, child, start=None, end=None):
    return _with_ids(backend.query_range(path, child, start, end))


def query_last(path, child, n):
    return _with_ids(backend.query_last(path, child, n))


def shallow(path):
    return backend.shallow(path)


def pmap(fn, items):
    """Run blocking DB calls in parallel."""
    return list(_pool.map(fn, items))

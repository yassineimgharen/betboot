"""JSON fetching with a disk cache and per-endpoint request spacing shared across runs."""
from pathlib import Path
import hashlib
import json
import threading
import time

import httpx


class SourceError(Exception):
    """A provider could not supply the requested data."""


class Deferred(SourceError):
    """The request would have to wait for a rate limit beyond this run's time budget."""


_lock = threading.Lock()


class Fetcher:
    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, deadline: float | None = None):
        """deadline: time.time() after which no rate-limit wait may end (None: wait as needed)."""
        self.cache_dir = cache_dir
        self.deadline = deadline
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=30, headers={'User-Agent': 'slipbot/1.0'})
        self._stamps = self.cache_dir / 'last-requests.json'

    def _path(self, key):
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest()[:24] + '.json')

    def cached(self, key, max_age):
        path = self._path(key)
        try:
            if max_age is None or time.time() - path.stat().st_mtime <= max_age:
                return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        return None

    def store(self, key, payload):
        path = self._path(key)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)

    def wait_turn(self, slot, spacing):
        """Reserve the next permitted time in this slot (shared across threads and runs),
        then sleep until it. Raises Deferred if that time is past the deadline."""
        with _lock:
            try:
                stamps = json.loads(self._stamps.read_text())
            except (OSError, ValueError):
                stamps = {}
            now = time.time()
            when = max(now, stamps.get(slot, 0) + spacing)
            if self.deadline is not None and when > now and when > self.deadline:
                raise Deferred(f'{slot}: deferred by the provider rate limit')
            stamps[slot] = when
            self._stamps.write_text(json.dumps(stamps))
        if when > now:
            time.sleep(when - now)

    def get(self, url, *, params=None, headers=None, slot, spacing):
        """GET JSON, returning (payload, response headers)."""
        self.wait_turn(slot, spacing)
        try:
            response = self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise SourceError(f'{slot}: network error ({type(exc).__name__})') from exc
        if response.status_code == 429:
            raise SourceError(f'{slot}: rate limited')
        if response.status_code >= 400:
            raise SourceError(f'{slot}: HTTP {response.status_code}')
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(f'{slot}: invalid JSON') from exc
        return payload, response.headers

    def get_cached(self, url, *, cache_key, max_age, validate=lambda p: p, stale_ok=False, **kw):
        """GET JSON through the cache (max_age None: never expires).
        `validate` returns what to store or raises SourceError. With stale_ok, an expired
        cached copy is returned when a fresh request is deferred by the rate limit."""
        if (hit := self.cached(cache_key, max_age)) is not None:
            return hit
        try:
            payload, _ = self.get(url, **kw)
        except Deferred:
            if stale_ok and (stale := self.cached(cache_key, None)) is not None:
                return stale
            raise
        result = validate(payload)
        self.store(cache_key, result)
        return result

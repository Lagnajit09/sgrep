import hashlib
import json
from pathlib import Path


class Cache:
    """On-disk verdict cache keyed by (model, query, chunk content).

    Re-running a query, or re-scanning after editing a few files, only pays for
    the chunks that actually changed — everything else is served from disk. The
    query is passed per call (not fixed at construction) so one cache instance can
    serve a batch of several queries in the same run.
    """

    def __init__(self, path, model, enabled=True):
        self.enabled = enabled
        self.path = Path(path)
        self._model = model
        self.data = {}
        self.hits = 0
        if enabled and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def _key(self, query, chunk):
        raw = f"{self._model}\x00{query}\x00{chunk.file}\x00{chunk.text}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def has(self, query, chunk):
        """Membership check with no side effects (does not count as a hit)."""
        return self.enabled and self._key(query, chunk) in self.data

    def get(self, query, chunk):
        if not self.enabled:
            return None
        v = self.data.get(self._key(query, chunk))
        if v is not None:
            self.hits += 1
        return v

    def put(self, query, chunk, verdict_dict):
        if self.enabled:
            self.data[self._key(query, chunk)] = verdict_dict

    def save(self):
        if not self.enabled:
            return
        try:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            pass

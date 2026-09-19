import json
from pathlib import Path

from ..models import Chunk


class ChunkCache:
    """Per-file chunk cache keyed by (mtime_ns, size).

    Parsing 100+ files with tree-sitter is the slowest cold step; if a file is
    unchanged since last run we reuse its chunks and skip the parse entirely.
    Only files seen this run are persisted, so deleted files fall out.
    """

    def __init__(self, path, enabled=True):
        self.enabled = enabled
        self.path = Path(path)
        self.old = {}
        self.result = {}
        self.reused_files = 0
        if enabled and self.path.exists():
            try:
                self.old = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.old = {}

    def get(self, rel, sig):
        if not self.enabled:
            return None
        e = self.old.get(rel)
        if e and e.get("m") == sig[0] and e.get("s") == sig[1]:
            self.reused_files += 1
            self.result[rel] = e
            return [Chunk(file=rel, start_line=c[0], end_line=c[1], text=c[2]) for c in e["c"]]
        return None

    def put(self, rel, sig, chunks):
        if self.enabled:
            self.result[rel] = {
                "m": sig[0], "s": sig[1],
                "c": [[c.start_line, c.end_line, c.text] for c in chunks],
            }

    def save(self):
        if not self.enabled:
            return
        try:
            self.path.write_text(json.dumps(self.result), encoding="utf-8")
        except OSError:
            pass

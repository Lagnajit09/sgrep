"""Optional warm daemon: keep the process + pre-filter model resident so repeated
scans skip the ~1-2s Python-startup + Model2Vec-load floor.

Design goals: zero-config, localhost-only, and *fail-safe*. `sgrep scan --daemon`
talks to it for `--json` output; on any problem it silently falls back to an
in-process scan, so the daemon can never break a scan — it only speeds one up.

Security: binds 127.0.0.1 only and requires a per-session token (auto-written to
~/.sgrep/daemon.json, readable only by the user). API keys are never sent over the
wire — the daemon loads the caller's `.env` from the cwd it passes in.
"""
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib import request as _urlrequest
from urllib.error import URLError

from .cache import Cache
from .chunkers import chunk_files
from .chunkers.cache import ChunkCache
from .config import cache_dir_for, load_config
from .discover import DEFAULT_EXTS, discover_files, load_ignore_patterns
from .engine import scan_many
from .render import _result_dict

STATE_FILE = Path.home() / ".sgrep" / "daemon.json"
DEFAULT_PORT = 8765

_WARM_PF = {}          # kind -> PreFilter instance (model loaded once)
_PF_LOCK = threading.Lock()


def _warm_prefilter(kind):
    """Return a cached pre-filter, loading (and keeping) the model once per process."""
    if kind in ("none",):
        return None
    with _PF_LOCK:
        if kind not in _WARM_PF:
            from .prefilter import get_prefilter
            _WARM_PF[kind] = get_prefilter(kind)
        return _WARM_PF[kind]


def _prefilter_many(queries, chunks, *, prefilter, topk, min_k, no_adaptive):
    if prefilter == "none" or not topk or len(chunks) <= topk:
        return [chunks for _ in queries]
    pf = _warm_prefilter(prefilter)
    if pf is None:
        return [chunks for _ in queries]
    from .prefilter.select import adaptive_select
    with _PF_LOCK:  # StaticModel.encode across threads — serialize to be safe (it's fast)
        score_lists = pf.rank_many(queries, chunks)
    per_query = []
    for scores in score_lists:
        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        if no_adaptive:
            kept = min(topk, len(chunks))
        else:
            kept, _warn, _reason = adaptive_select([scores[i] for i in order], min_k, topk)
        per_query.append([chunks[i] for i in order[:kept]])
    return per_query


def scan_request(payload):
    """Run one scan for a JSON request. Returns a JSON-serializable dict."""
    from .cli import _pick_client  # reuse the exact provider-selection logic

    queries = [q for q in (payload.get("queries") or []) if q]
    if not queries:
        return {"error": "no queries"}
    cwd = payload.get("cwd") or os.getcwd()
    load_config(Path(cwd))  # OS env > caller's .env > global ~/.config/sgrep/.env

    root = Path(payload.get("path") or ".").resolve()
    if not root.exists():
        return {"error": f"path not found: {root}"}

    opt = lambda k, d: payload.get(k, d)  # noqa: E731
    no_cache = bool(opt("no_cache", False))
    exts = {e if e.startswith(".") else "." + e for e in (opt("ext", None) or [])} or DEFAULT_EXTS
    ignore = load_ignore_patterns(root)
    cache_dir = cache_dir_for(root)  # global, keyed by repo — same as the non-daemon path
    chunk_cache = ChunkCache(cache_dir / ".sgrep-chunkcache.json", enabled=not no_cache)
    files = discover_files(root, exts=exts, include=opt("include", None),
                           exclude=opt("exclude", None), ignore_patterns=ignore)
    if not files:
        return {"queries": queries, "results": [], "meta": {"files": 0, "chunks": 0}}
    chunks = chunk_files(files, root, window=opt("window", 60), overlap=opt("overlap", 10), cache=chunk_cache)
    chunk_cache.save()

    candidates_per_query = _prefilter_many(
        queries, chunks, prefilter=opt("prefilter", "auto"), topk=opt("topk", 50),
        min_k=opt("min_k", 8), no_adaptive=opt("no_adaptive", False))

    model = opt("model", "jev-latest")
    cache = Cache(cache_dir / ".sgrep-cache.json", model, enabled=not no_cache)
    client, mode, _note = _pick_client(SimpleNamespace(
        mock=bool(opt("mock", False)), provider=opt("provider", "auto"),
        model=model, concurrency=opt("concurrency", 16)))
    conc = min(opt("concurrency", 16), getattr(client, "max_concurrency", opt("concurrency", 16)))

    jobs = list(zip(queries, candidates_per_query))
    t0 = time.perf_counter()
    verdict_lists = scan_many(jobs, client, concurrency=conc, cache=cache)
    elapsed = time.perf_counter() - t0
    cache.save()
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass

    threshold, top = opt("threshold", 0.6), opt("top", 20)
    results = [_result_dict(v, q, threshold, top, None) for v, q in zip(verdict_lists, queries)]
    total = sum(len(c) for c in candidates_per_query)
    return {
        "queries": queries,
        "elapsed_seconds": round(elapsed, 3),
        "results": results,
        "meta": {"files": len(files), "chunks": len(chunks), "candidates": total,
                 "cached": cache.hits, "mode": mode, "elapsed_seconds": round(elapsed, 3)},
    }


# ---------------------------------------------------------------------------
# server

def _make_handler(token):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._reply(200, {"ok": True})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/scan":
                self._reply(404, {"error": "not found"})
                return
            if self.headers.get("X-Sgrep-Token") != token:
                self._reply(403, {"error": "bad token"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                self._reply(200, scan_request(payload))
            except Exception as e:  # noqa: BLE001 -- never crash the server on one bad request
                self._reply(500, {"error": f"{type(e).__name__}: {e}"})

        def log_message(self, *a):  # silence default per-request stderr spam
            pass

    return Handler


def _write_state(host, port, token):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(
        {"host": host, "port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def serve(host="127.0.0.1", port=DEFAULT_PORT, prefilter="auto"):
    token = secrets.token_hex(16)
    load_config(Path.cwd())  # so the resident process has the key (OS env / .env / global)
    print(f"sgrep daemon: warming pre-filter model…", file=sys.stderr)
    _warm_prefilter(prefilter)  # pay the model-load cost once, now
    httpd = ThreadingHTTPServer((host, port), _make_handler(token))
    _write_state(host, port, token)
    print(f"sgrep daemon ready on http://{host}:{port}  (state: {STATE_FILE})", file=sys.stderr)
    print("  scans via:  sgrep scan \"...\" <path> --daemon --json", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nsgrep daemon: shutting down.", file=sys.stderr)
    finally:
        httpd.server_close()
        try:
            if STATE_FILE.exists() and json.loads(STATE_FILE.read_text()).get("pid") == os.getpid():
                STATE_FILE.unlink()
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------------------
# client

def daemon_scan(payload, timeout=120):
    """Send a scan to a running daemon. Returns the result dict, or None if the
    daemon is unreachable / errors (caller then falls back to in-process)."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        url = f"http://{state['host']}:{state['port']}/scan"
        data = json.dumps(payload).encode("utf-8")
        req = _urlrequest.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json", "X-Sgrep-Token": state["token"]})
        with _urlrequest.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
        return None if isinstance(out, dict) and out.get("error") else out
    except (OSError, ValueError, KeyError, URLError):
        return None


def daemon_running():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        url = f"http://{state['host']}:{state['port']}/health"
        with _urlrequest.urlopen(url, timeout=2) as r:
            return json.loads(r.read()).get("ok") is True
    except (OSError, ValueError, KeyError, URLError):
        return False

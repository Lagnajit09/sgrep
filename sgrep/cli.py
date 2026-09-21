import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

from .cache import Cache
from .chunkers import chunk_files
from .chunkers.cache import ChunkCache
from .clients.mock import MockClient
from .clients.typesafe import TypeSafeClient
from .clients.vercel import VercelJevClient
from .config import cache_dir_for, load_config
from .discover import DEFAULT_EXTS, discover_files, load_ignore_patterns
from .engine import scan_many
from .render import render_many


def _dim(s):
    return f"\033[2m{s}\033[0m"


@contextlib.contextmanager
def _spinner(text):
    """Animated spinner on stderr in a real terminal; a plain dim line otherwise."""
    try:
        from rich.console import Console
        console = Console(stderr=True)
        if console.is_terminal:
            with console.status(f"[dim]{text}…[/dim]", spinner="dots"):
                yield
            return
    except ImportError:
        pass
    print(_dim(f"  {text}…"), file=sys.stderr)
    yield


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="sgrep",
        description="Semantic grep for codebases, powered by Jev (TypeSafe System One).",
    )
    from . import __version__
    parser.add_argument("--version", action="version", version=f"sgrep {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="Semantic scan of a codebase for a natural-language query.")
    p.add_argument("query", nargs="?", help="e.g. \"which code handles authentication\"")
    p.add_argument("path", nargs="?", default=".", help="root to scan (default: .)")
    p.add_argument("-q", "--query", dest="extra_queries", action="append", metavar="QUERY",
                   help="additional query, repeatable (max 3 total). Batch mode shares one "
                        "startup + one model load + one parse across all queries.")
    p.add_argument("--threshold", type=float, default=0.6, help="min match probability (default 0.6)")
    p.add_argument("--top", type=int, default=20, help="max hits to show (default 20)")
    p.add_argument("--window", type=int, default=60, help="fallback chunk size in lines (default 60)")
    p.add_argument("--overlap", type=int, default=10, help="fallback chunk overlap in lines (default 10)")
    p.add_argument("--concurrency", type=int, default=16, help="parallel Jev calls (default 16)")
    p.add_argument("--ext", action="append", help="restrict to extension(s), repeatable, e.g. --ext .py")
    p.add_argument("--include", action="append", help="glob(s) to include, repeatable")
    p.add_argument("--exclude", action="append", help="glob(s) to exclude, repeatable")
    p.add_argument("--model", default="jev-latest", help="Jev model id (default jev-latest)")
    p.add_argument("--mock", action="store_true", help="use the offline mock Jev (no API calls)")
    p.add_argument("--provider", choices=["auto", "vercel", "typesafe", "mock"], default="auto",
                   help="Jev provider (default auto: Vercel AI Gateway, then TypeSafe direct)")
    p.add_argument("--no-cache", action="store_true", help="disable the on-disk caches")
    p.add_argument(
        "--prefilter", choices=["auto", "hybrid", "semantic", "lexical", "none"], default="auto",
        help="local pre-filter before Jev when chunks exceed --topk (default auto = hybrid)",
    )
    p.add_argument("--topk", type=int, default=50,
                   help="max candidates sent to Jev after pre-filter (default 50; 0 = exhaustive)")
    p.add_argument("--min-k", type=int, default=8, help="adaptive floor: always keep >= this many (default 8)")
    p.add_argument("--no-adaptive", action="store_true", help="hard top-k cut instead of adaptive knee")
    p.add_argument("--json", action="store_true", dest="as_json", help="emit JSON instead of a report")
    p.add_argument("--daemon", action="store_true",
                   help="use a warm `sgrep serve` daemon if running (skips startup/model-load; "
                        "JSON output; silently falls back to in-process if unavailable)")

    sv = sub.add_parser("serve", help="Run a warm background daemon so repeated --daemon scans "
                                      "skip the ~1-2s startup + model-load each time.")
    sv.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1, localhost only)")
    sv.add_argument("--port", type=int, default=8765, help="bind port (default 8765)")
    return parser


def _pick_client(args):
    """Return (client, mode, note).

    --provider auto prefers TypeSafe direct: the Vercel free tier is too rate-limited for
    sgrep's per-chunk fan-out (even a 2-chunk scan 429s out), so it's opt-in via
    --provider vercel (use it if your Vercel account has higher limits). The non-preferred
    provider is still tried as a fallback before giving up to mock.
    """
    prov = "mock" if args.mock else args.provider
    if prov == "mock":
        return MockClient(), "mock (offline)", ""
    if prov == "auto":
        prov = "typesafe" if os.environ.get("TYPESAFE_API_KEY") else "vercel"

    note = ""
    for p in (prov, "typesafe" if prov == "vercel" else "vercel"):  # preferred, then fallback
        if p == "vercel":
            vkey = os.environ.get("VERCEL_AI_GATEWAY_API_KEY")
            if not vkey:
                continue
            try:
                c = VercelJevClient(api_key=vkey, pool=args.concurrency)
                ok, err = c.healthcheck()
                if ok:
                    return c, "Jev · Vercel AI Gateway", note
                c.close()
                note = note or _dim(f"  Vercel unavailable ({(err or '')[:70]})")
            except Exception as e:  # noqa: BLE001
                note = note or _dim(f"  Vercel init failed ({e})")
        elif p == "typesafe":
            if not os.environ.get("TYPESAFE_API_KEY"):
                continue
            try:
                return TypeSafeClient(model=args.model, pool=args.concurrency), "Jev · TypeSafe direct", note
            except Exception as e:  # noqa: BLE001
                note = note or _dim(f"  TypeSafe init failed ({e})")
    return MockClient(), "mock (offline; no working provider)", note


MAX_QUERIES = 3


def _collect_queries(args):
    """Merge the positional query and any -q/--query into an ordered, de-duped list.

    Capped at MAX_QUERIES: tracing a flow wants a few *broad* queries, not many
    narrow ones, and a hard cap keeps the batch fan-out from stressing rate limits.
    Returns (queries, error_message)."""
    raw = []
    if args.query:
        raw.append(args.query)
    if args.extra_queries:
        raw.extend(args.extra_queries)
    seen, queries = set(), []
    for q in raw:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
    if not queries:
        return [], "provide a query (positional) and/or one or more -q/--query"
    if len(queries) > MAX_QUERIES:
        return [], (f"at most {MAX_QUERIES} queries per run (got {len(queries)}); "
                    "trace a flow with a few broad queries, not many narrow ones")
    return queries, ""


def _prefilter_many(args, queries, chunks):
    """Local top-k funnel for a batch of queries. Chunk-side work (embeddings, BM25
    stats) runs ONCE via rank_many; each query gets its own adaptive candidate set.

    Returns (candidates_per_query, note, warns_per_query)."""
    empty_warns = ["" for _ in queries]
    if args.prefilter == "none" or not args.topk or len(chunks) <= args.topk:
        return [chunks for _ in queries], "", empty_warns
    from .prefilter import get_prefilter
    from .prefilter.select import adaptive_select

    pf = get_prefilter(args.prefilter)
    if pf is None:
        return [chunks for _ in queries], "", empty_warns
    t = time.perf_counter()
    score_lists = pf.rank_many(queries, chunks)
    per_query, warns = [], []
    for scores in score_lists:
        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        if args.no_adaptive:
            kept, warn, reason = min(args.topk, len(chunks)), False, ""
        else:
            kept, warn, reason = adaptive_select([scores[i] for i in order], args.min_k, args.topk)
        per_query.append([chunks[i] for i in order[:kept]])
        warns.append(f"\033[33m  ⚠ under-recall: {reason} — use --topk 0 for exhaustive\033[0m" if warn else "")
    total = sum(len(c) for c in per_query)
    span = f"{len(chunks)} → {total}" + (f" across {len(queries)} queries" if len(queries) > 1 else "")
    note = _dim(f"  prefilter[{pf.name}]: {span} ({time.perf_counter() - t:.2f}s local)")
    return per_query, note, warns


def _force_utf8_output():
    """Windows pipes/redirects default to cp1252, which can't encode the score-bar
    glyphs (█ ░ ·) — encoding then crashes with UnicodeEncodeError. Reconfigure the
    streams to UTF-8 so any output path (rich, plain, --json, spinner) is safe."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _print_daemon_json(res, queries):
    """Print a daemon result matching the in-process --json shapes exactly."""
    if len(queries) == 1 and res.get("results"):
        out = dict(res["results"][0])
        out["elapsed_seconds"] = res.get("elapsed_seconds")
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps({"queries": res.get("queries", queries),
                          "elapsed_seconds": res.get("elapsed_seconds"),
                          "results": res.get("results", [])}, indent=2))


def _daemon_payload(args, queries, root):
    return {
        "queries": queries, "path": str(root), "cwd": str(Path.cwd()),
        "threshold": args.threshold, "top": args.top, "window": args.window,
        "overlap": args.overlap, "concurrency": args.concurrency, "ext": args.ext,
        "include": args.include, "exclude": args.exclude, "model": args.model,
        "mock": args.mock, "provider": args.provider, "no_cache": args.no_cache,
        "prefilter": args.prefilter, "topk": args.topk, "min_k": args.min_k,
        "no_adaptive": args.no_adaptive,
    }


def main(argv=None):
    _force_utf8_output()
    args = _build_parser().parse_args(argv)

    if args.cmd == "serve":
        from .daemon import serve
        serve(host=args.host, port=args.port)
        return 0

    load_config(Path.cwd())

    queries, qerr = _collect_queries(args)
    if qerr:
        print(f"sgrep: {qerr}", file=sys.stderr)
        return 2

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 2

    if args.daemon:
        if not args.as_json:
            print(_dim("  (--daemon serves JSON; running in-process for the rich report)"), file=sys.stderr)
        else:
            from .daemon import daemon_scan
            with _spinner("querying warm daemon"):
                res = daemon_scan(_daemon_payload(args, queries, root))
            if res is not None:
                _print_daemon_json(res, queries)
                return 0
            print(_dim("  (daemon unavailable; running in-process)"), file=sys.stderr)

    exts = {e if e.startswith(".") else "." + e for e in (args.ext or [])} or DEFAULT_EXTS
    ignore = load_ignore_patterns(root)
    cachedir = cache_dir_for(root)
    chunk_cache = ChunkCache(cachedir / ".sgrep-chunkcache.json", enabled=not args.no_cache)

    with _spinner("discovering & parsing files"):
        files = discover_files(root, exts=exts, include=args.include, exclude=args.exclude, ignore_patterns=ignore)
        if not files:
            print("no source files found.", file=sys.stderr)
            return 1
        chunks = chunk_files(files, root, window=args.window, overlap=args.overlap, cache=chunk_cache)
        chunk_cache.save()
    reused = f" · {chunk_cache.reused_files} file(s) cached" if chunk_cache.reused_files else ""
    if len(queries) > 1:
        print(_dim(f"  batch: {len(queries)} queries · shared parse + model load"), file=sys.stderr)
    print(_dim(f"  {len(files)} files · {len(chunks)} chunks{reused}"), file=sys.stderr)

    with _spinner("pre-filtering"):
        candidates_per_query, pf_note, pf_warns = _prefilter_many(args, queries, chunks)
    if pf_note:
        print(pf_note, file=sys.stderr)
    for warn in pf_warns:
        if warn:
            print(warn, file=sys.stderr)

    cache = Cache(cachedir / ".sgrep-cache.json", args.model, enabled=not args.no_cache)

    with _spinner("connecting to Jev"):
        client, mode, client_note = _pick_client(args)
    if client_note:
        print(client_note, file=sys.stderr)
    conc = min(args.concurrency, getattr(client, "max_concurrency", args.concurrency))

    def progress(done, total):
        if total and (done % 10 == 0 or done == total):
            print(f"\r{_dim(f'  judged {done}/{total}')}   ", end="", file=sys.stderr, flush=True)

    jobs = list(zip(queries, candidates_per_query))
    total_candidates = sum(len(c) for c in candidates_per_query)
    t0 = time.perf_counter()
    verdict_lists = scan_many(jobs, client, concurrency=conc, progress=progress, cache=cache)
    elapsed = time.perf_counter() - t0
    print("", file=sys.stderr)

    cache.save()
    transport = getattr(client, "transport", "n/a")
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass

    judged = total_candidates - cache.hits
    rps = judged / elapsed if elapsed > 0 and judged else 0.0
    print(
        _dim(f"  {judged} judged · {cache.hits} cached · {rps:.0f} req/s · {elapsed:.2f}s · {transport}"),
        file=sys.stderr,
    )

    render_many(verdict_lists, queries, threshold=args.threshold, top=args.top,
                as_json=args.as_json, mode=mode, elapsed=elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

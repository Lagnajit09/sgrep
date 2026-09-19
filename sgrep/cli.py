import argparse
import os
import sys
import time
from pathlib import Path

from .cache import Cache
from .chunkers import chunk_files
from .clients.mock import MockClient
from .clients.typesafe import TypeSafeClient
from .config import load_env
from .discover import DEFAULT_EXTS, discover_files
from .engine import scan
from .render import render


def _dim(s):
    return f"\033[2m{s}\033[0m"


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="sgrep",
        description="Semantic grep for codebases, powered by Jev (TypeSafe System One).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="Semantic scan of a codebase for a natural-language query.")
    p.add_argument("query", help="e.g. \"which code handles authentication\"")
    p.add_argument("path", nargs="?", default=".", help="root to scan (default: .)")
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
    p.add_argument("--no-cache", action="store_true", help="disable the on-disk verdict cache")
    p.add_argument(
        "--prefilter", choices=["auto", "hybrid", "semantic", "lexical", "none"], default="auto",
        help="local pre-filter before Jev when chunks exceed --topk (default auto = hybrid)",
    )
    p.add_argument(
        "--topk", type=int, default=50,
        help="max candidates sent to Jev after pre-filter (default 50; 0 = no cap / exhaustive)",
    )
    p.add_argument("--min-k", type=int, default=8, help="adaptive floor: always keep >= this many (default 8)")
    p.add_argument("--no-adaptive", action="store_true", help="hard top-k cut instead of adaptive knee detection")
    p.add_argument("--json", action="store_true", dest="as_json", help="emit JSON instead of a report")
    return parser


def _pick_client(args):
    if args.mock:
        return MockClient(), "mock (offline)"
    if os.environ.get("TYPESAFE_API_KEY"):
        try:
            return TypeSafeClient(model=args.model, pool=args.concurrency), f"Jev live: {args.model}"
        except Exception as e:  # noqa: BLE001
            print(f"  (falling back to mock: {e})", file=sys.stderr)
    return MockClient(), "mock (offline; no TYPESAFE_API_KEY)"


def _prefilter(args, chunks):
    """Return the candidate chunks after the optional local top-k funnel."""
    if args.prefilter == "none" or not args.topk or len(chunks) <= args.topk:
        return chunks
    from .prefilter import get_prefilter
    from .prefilter.select import adaptive_select

    pf = get_prefilter(args.prefilter)
    if pf is None:
        return chunks

    t = time.perf_counter()
    scores = pf.rank(args.query, chunks)
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    if args.no_adaptive:
        kept, warn, reason = min(args.topk, len(chunks)), False, ""
    else:
        kept, warn, reason = adaptive_select([scores[i] for i in order], args.min_k, args.topk)
    candidates = [chunks[i] for i in order[:kept]]
    print(
        _dim(f"  prefilter[{pf.name}]: {len(chunks)} → {len(candidates)} ({time.perf_counter() - t:.2f}s local)"),
        file=sys.stderr,
    )
    if warn:
        print(f"\033[33m  ⚠ under-recall: {reason} — use --topk 0 for exhaustive\033[0m", file=sys.stderr)
    return candidates


def main(argv=None):
    args = _build_parser().parse_args(argv)
    load_env(Path.cwd())

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 2

    exts = {e if e.startswith(".") else "." + e for e in (args.ext or [])} or DEFAULT_EXTS
    files = discover_files(root, exts=exts, include=args.include, exclude=args.exclude)
    if not files:
        print("no source files found.", file=sys.stderr)
        return 1
    chunks = chunk_files(files, root, window=args.window, overlap=args.overlap)
    print(_dim(f"  {len(files)} files · {len(chunks)} chunks"), file=sys.stderr)
    candidates = _prefilter(args, chunks)

    client, mode = _pick_client(args)
    cache = Cache(Path.cwd() / ".sgrep-cache.json", args.model, args.query, enabled=not args.no_cache)

    def progress(done, total):
        if total and (done % 10 == 0 or done == total):
            print(f"\r{_dim(f'  judged {done}/{total}')}   ", end="", file=sys.stderr, flush=True)

    warm = 0.0
    if sum(1 for c in candidates if not cache.has(c)) and hasattr(client, "warmup"):
        tw = time.perf_counter()
        client.warmup()
        warm = time.perf_counter() - tw

    t0 = time.perf_counter()
    verdicts = scan(candidates, client, args.query, concurrency=args.concurrency, progress=progress, cache=cache)
    elapsed = time.perf_counter() - t0
    print("", file=sys.stderr)

    cache.save()
    transport = getattr(client, "transport", "n/a")
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass

    judged = len(candidates) - cache.hits
    rps = judged / elapsed if elapsed > 0 and judged else 0.0
    print(
        _dim(f"  {judged} judged · {cache.hits} cached · {rps:.0f} req/s · {warm + elapsed:.2f}s · {transport}"),
        file=sys.stderr,
    )

    render(verdicts, args.query, threshold=args.threshold, top=args.top,
           as_json=args.as_json, mode=mode, elapsed=elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

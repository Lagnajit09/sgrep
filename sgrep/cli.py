import argparse
import os
import sys
import time
from pathlib import Path

from .cache import Cache
from .chunkers.window import chunk_files
from .clients.mock import MockClient
from .clients.typesafe import TypeSafeClient
from .config import load_env
from .discover import DEFAULT_EXTS, discover_files
from .engine import scan
from .render import render


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
    p.add_argument("--window", type=int, default=60, help="chunk size in lines (default 60)")
    p.add_argument("--overlap", type=int, default=10, help="chunk overlap in lines (default 10)")
    p.add_argument("--concurrency", type=int, default=32, help="parallel Jev calls (default 32)")
    p.add_argument("--ext", action="append", help="restrict to extension(s), repeatable, e.g. --ext .py")
    p.add_argument("--include", action="append", help="glob(s) to include, repeatable")
    p.add_argument("--exclude", action="append", help="glob(s) to exclude, repeatable")
    p.add_argument("--model", default="jev-latest", help="Jev model id (default jev-latest)")
    p.add_argument("--mock", action="store_true", help="use the offline mock Jev (no API calls)")
    p.add_argument("--no-cache", action="store_true", help="disable the on-disk verdict cache")
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

    client, mode = _pick_client(args)
    cache = Cache(Path.cwd() / ".sgrep-cache.json", args.model, args.query, enabled=not args.no_cache)
    print(
        f"  scanning {len(files)} file(s) -> {len(chunks)} chunk(s) via {mode} ...",
        file=sys.stderr,
    )

    def progress(done, total):
        if total and (done % 20 == 0 or done == total):
            print(f"\r  judged {done}/{total} chunks", end="", file=sys.stderr, flush=True)

    # Warm the connection once (only if there's uncached work) so concurrent
    # HTTP/2 streams don't race a cold socket. Skipped when fully cached, so a
    # repeat query stays instant.
    warm = 0.0
    misses = sum(1 for c in chunks if not cache.has(c))
    if misses and hasattr(client, "warmup"):
        tw = time.perf_counter()
        client.warmup()
        warm = time.perf_counter() - tw

    t0 = time.perf_counter()
    verdicts = scan(
        chunks, client, args.query,
        concurrency=args.concurrency, progress=progress, cache=cache,
    )
    elapsed = time.perf_counter() - t0
    print("", file=sys.stderr)

    cache.save()
    transport = getattr(client, "transport", "n/a")
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass

    judged = len(chunks) - cache.hits
    rps = judged / elapsed if elapsed > 0 and judged else 0.0
    per = elapsed / judged * 1000 if judged else 0.0
    warm_note = f"{warm:.2f}s connect + " if warm else ""
    print(
        f"  {len(chunks)} chunks in {warm_note}{elapsed:.2f}s scan  "
        f"({cache.hits} cached, {judged} judged; {rps:.1f} req/s, ~{per:.0f} ms/chunk, {transport})",
        file=sys.stderr,
    )

    render(
        verdicts, args.query, threshold=args.threshold, top=args.top,
        as_json=args.as_json, mode=mode, elapsed=elapsed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

    def _add_scan_flags(sp):
        sp.add_argument("--threshold", type=float, default=0.6, help="min match probability (default 0.6)")
        sp.add_argument("--top", type=int, default=20, help="max hits/seeds to keep (default 20)")
        sp.add_argument("--window", type=int, default=60, help="fallback chunk size in lines (default 60)")
        sp.add_argument("--overlap", type=int, default=10, help="fallback chunk overlap in lines (default 10)")
        sp.add_argument("--concurrency", type=int, default=16, help="parallel Jev calls (default 16)")
        sp.add_argument("--ext", action="append", help="restrict to extension(s), repeatable, e.g. --ext .py")
        sp.add_argument("--include", action="append", help="glob(s) to include, repeatable")
        sp.add_argument("--exclude", action="append", help="glob(s) to exclude, repeatable")
        sp.add_argument("--model", default="jev-latest", help="Jev model id (default jev-latest)")
        sp.add_argument("--mock", action="store_true", help="use the offline mock Jev (no API calls)")
        sp.add_argument("--provider", choices=["auto", "vercel", "typesafe", "mock"], default="auto",
                        help="Jev provider (default auto: TypeSafe direct, then Vercel)")
        sp.add_argument("--no-cache", action="store_true", help="disable the on-disk caches")
        sp.add_argument("--prefilter", choices=["auto", "hybrid", "semantic", "lexical", "none"],
                        default="auto", help="local pre-filter before Jev when chunks exceed --topk")
        sp.add_argument("--topk", type=int, default=50,
                        help="max candidates sent to Jev after pre-filter (default 50; 0 = exhaustive)")
        sp.add_argument("--min-k", type=int, default=8, help="adaptive floor: keep >= this many (default 8)")
        sp.add_argument("--no-adaptive", action="store_true", help="hard top-k cut instead of adaptive knee")
        sp.add_argument("--json", action="store_true", dest="as_json", help="emit JSON instead of a report")

    p = sub.add_parser("scan", help="Semantic scan of a codebase for a natural-language query.")
    p.add_argument("query", nargs="?", help="e.g. \"which code handles authentication\"")
    p.add_argument("path", nargs="?", default=".", help="root to scan (default: .)")
    p.add_argument("-q", "--query", dest="extra_queries", action="append", metavar="QUERY",
                   help="additional query, repeatable (max 3 total). Batch mode shares one "
                        "startup + one model load + one parse across all queries.")
    _add_scan_flags(p)
    p.add_argument("--daemon", action="store_true",
                   help="use a warm `sgrep serve` daemon if running (skips startup/model-load; "
                        "JSON output; silently falls back to in-process if unavailable)")

    tr = sub.add_parser("trace", help="Trace a flow: semantic seeds (Jev) expanded along the "
                                      "graphify call graph (callers + callees).")
    tr.add_argument("query", nargs="?", help="e.g. \"how are requests authenticated\"")
    tr.add_argument("path", nargs="?", default=".", help="root to scan (default: .)")
    tr.add_argument("--symbol", help="seed from an exact graph node instead of a Jev query: "
                                     "a name (stream_execution) or file:line (executor.py:15)")
    tr.add_argument("--hops", type=int, default=2, help="call-graph expansion depth per seed (default 2)")
    tr.add_argument("--relations", default="calls,method",
                    help="edge types to follow (default calls,method; also uses,contains,inherits,imports_from)")
    tr.add_argument("--max-nodes", type=int, default=24, help="cap expanded nodes per seed (default 24)")
    tr.add_argument("--include-tests", action="store_true", help="keep test files in the trace (excluded by default)")
    tr.add_argument("--refresh", action="store_true", help="rebuild the graph via `graphify update` first")
    _add_scan_flags(tr)
    tr.set_defaults(top=5)  # a handful of precise seeds, not 20

    im = sub.add_parser("impact", help="Blast radius: what (transitively) depends on the code "
                                       "matching the query, via the graphify graph.")
    im.add_argument("query", nargs="?", help="e.g. \"the function that verifies JWTs\"")
    im.add_argument("path", nargs="?", default=".", help="root to scan (default: .)")
    im.add_argument("--symbol", help="seed from an exact graph node instead of a Jev query: "
                                     "a name (build_worker_payload) or file:line (worker.py:52)")
    im.add_argument("--hops", type=int, default=3, help="how many caller hops to follow (default 3)")
    im.add_argument("--relations", default="calls,method,uses",
                    help="edge types to follow upstream (default calls,method,uses)")
    im.add_argument("--max-nodes", type=int, default=60, help="cap affected nodes per seed (default 60)")
    im.add_argument("--include-tests", action="store_true", help="count test files (excluded by default)")
    im.add_argument("--refresh", action="store_true", help="rebuild the graph via `graphify update` first")
    _add_scan_flags(im)
    im.set_defaults(top=5)  # seed a few entry points, not one — better blast-radius coverage

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


def _scan_one(args, query, root):
    """Run the semantic pipeline for a single query; return (ranked Verdicts, mode).

    If a warm `sgrep serve` daemon is running, the seed scan goes through it — so
    `trace`/`impact` skip the ~2s model-load + parse that they'd otherwise pay on every
    call. Fail-safe: any daemon problem falls back to the in-process pipeline below.
    """
    from .daemon import daemon_scan
    res = daemon_scan(_daemon_payload(args, [query], root))
    if res is not None:
        from .models import Chunk, Verdict
        hits = (res.get("results") or [{}])[0].get("hits", [])
        verdicts = [Verdict(chunk=Chunk(h["file"], h["start_line"], h["end_line"], ""),
                            score=h.get("score", 0.0), match=h.get("match", 0.0),
                            confidence=h.get("confidence")) for h in hits]
        print(_dim("  seeds via warm daemon"), file=sys.stderr)
        return sorted(verdicts, key=lambda v: v.rank_key, reverse=True), \
            (res.get("meta") or {}).get("mode", "Jev · daemon")

    exts = {e if e.startswith(".") else "." + e for e in (args.ext or [])} or DEFAULT_EXTS
    ignore = load_ignore_patterns(root)
    cachedir = cache_dir_for(root)
    chunk_cache = ChunkCache(cachedir / ".sgrep-chunkcache.json", enabled=not args.no_cache)
    with _spinner("discovering & parsing files"):
        files = discover_files(root, exts=exts, include=args.include, exclude=args.exclude,
                               ignore_patterns=ignore)
        if not files:
            return [], ""
        chunks = chunk_files(files, root, window=args.window, overlap=args.overlap, cache=chunk_cache)
        chunk_cache.save()
    print(_dim(f"  {len(files)} files · {len(chunks)} chunks"), file=sys.stderr)
    with _spinner("pre-filtering"):
        candidates_per_query, _note, _warns = _prefilter_many(args, [query], chunks)
    cache = Cache(cachedir / ".sgrep-cache.json", args.model, enabled=not args.no_cache)
    with _spinner("connecting to Jev"):
        client, mode, _cn = _pick_client(args)
    conc = min(args.concurrency, getattr(client, "max_concurrency", args.concurrency))
    verdicts = scan_many([(query, candidates_per_query[0])], client, concurrency=conc, cache=cache)[0]
    cache.save()
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    return sorted(verdicts, key=lambda v: v.rank_key, reverse=True), mode


def _seed_title(args):
    return args.query or f"--symbol {getattr(args, 'symbol', '')}"


def _node_loc(graph, nid):
    n = graph.nodes[nid]
    label = (n["label"] or "").splitlines()[0] if n["label"] else nid
    if len(label) > 48:
        label = label[:47] + "…"
    return label, f"{n['file']}:{n['line']}"


def _children_map(edges):
    cmap = {}
    for parent, child, rel, direction, depth in edges:
        cmap.setdefault(parent, []).append((child, rel, direction))
    return cmap


def _render_trace(seed_entries, graph, args, mode):
    C = {"dim": "\033[2m", "rst": "\033[0m", "cy": "\033[36m", "gn": "\033[32m",
         "yl": "\033[33m", "bd": "\033[1m"}
    n_expanded = sum(len(e) for _, nid, e in seed_entries if nid)
    print(f"\n  {C['bd']}{C['cy']}trace:{C['rst']} {_seed_title(args)}")
    print(f"  {C['dim']}{len(seed_entries)} seed(s) · {n_expanded} linked node(s) · "
          f"hops {args.hops} · rels {args.relations} · {mode}{C['rst']}\n")

    def walk(parent, cmap, prefix):
        kids = cmap.get(parent, [])
        for i, (child, rel, direction) in enumerate(kids):
            last = i == len(kids) - 1
            branch = "└─" if last else "├─"
            arrow = "→" if direction == "out" else "←"
            verb = rel if direction == "out" else f"{rel} by"
            label, loc = _node_loc(graph, child)
            print(f"  {prefix}{branch}{arrow} {C['dim']}{verb:<9}{C['rst']} {label}   "
                  f"{C['cy']}{loc}{C['rst']}")
            walk(child, cmap, prefix + ("   " if last else "│  "))

    for v, nid, edges in seed_entries:
        style = C["gn"] if v.match >= 0.75 else C["yl"]
        if nid:
            label, loc = _node_loc(graph, nid)
        else:
            label, loc = _preview_label(v), f"{v.chunk.file}:{v.chunk.start_line}"
        print(f"  {style}●{C['rst']} {C['bd']}{label}{C['rst']}   {C['cy']}{loc}{C['rst']}   "
              f"{C['dim']}match {v.match:.2f}{C['rst']}" + ("" if nid else f"  {C['dim']}(not in graph){C['rst']}"))
        walk(nid, _children_map(edges), "") if nid else None
        print()


def _preview_label(v):
    from .render import _preview
    return _preview(v.chunk.text)[:60] or v.chunk.file


def _trace_json(seed_entries, graph, args, graph_path, mode):
    seeds = []
    for v, nid, edges in seed_entries:
        node = None
        if nid:
            n = graph.nodes[nid]
            node = {"id": nid, "label": n["label"], "file": n["file"], "line": n["line"]}
        links = []
        for parent, child, rel, direction, depth in edges:
            cn = graph.nodes[child]
            links.append({"from": parent, "to": child, "relation": rel, "direction": direction,
                          "depth": depth, "to_label": cn["label"], "to_file": cn["file"],
                          "to_line": cn["line"]})
        seeds.append({"file": v.chunk.file, "start_line": v.chunk.start_line,
                      "end_line": v.chunk.end_line, "match": round(v.match, 3),
                      "node": node, "links": links})
    print(json.dumps({"query": _seed_title(args), "mode": mode, "hops": args.hops,
                      "relations": args.relations, "graph": str(graph_path), "seeds": seeds}, indent=2))


def _seed_node(graph, root, v):
    absf = str((root / v.chunk.file).resolve())
    return graph.node_for(absf, v.chunk.start_line, v.chunk.end_line)


def _synth_verdict(graph, nid):
    """A stand-in Verdict for a --symbol seed (already a known graph node)."""
    from .models import Chunk, Verdict
    n = graph.nodes[nid]
    return Verdict(chunk=Chunk(n["file"], n["line"], n["line"], n["label"] or ""),
                   score=1.0, match=1.0, confidence=None)


def _graph_and_seeds(args):
    """Shared setup for trace/impact. Returns (graph, graph_path, root, seed_pairs, mode)
    where seed_pairs is a list of (verdict, node_id_or_None); or an int exit code on error.
    Seeds come from `--symbol` (exact graph node, no Jev) or the semantic scan."""
    # `sgrep trace --symbol X <path>`: with no query, argparse puts the lone positional in
    # `query`; if it's actually a path, treat it as the path.
    if getattr(args, "symbol", None) and args.query and args.path == "." and Path(args.query).exists():
        args.path, args.query = args.query, None
    if not getattr(args, "symbol", None) and not args.query:
        print("sgrep: provide a query or --symbol", file=sys.stderr)
        return 2
    load_config(Path.cwd())
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 2
    from .graph import find_graph, load_graph, refresh
    graph_path, graph_root = find_graph(root)
    if graph_path is None:
        print("no graphify graph found (looked for graphify-out/graph.json at or above the "
              "scan path).", file=sys.stderr)
        print("build one first, e.g.:  graphify update <repo>", file=sys.stderr)
        return 2
    if args.refresh:
        with _spinner("refreshing graph (graphify update)"):
            ok, msg = refresh(graph_root)
        print(_dim(f"  graph refresh: {'ok' if ok else 'FAILED'} — {msg}"), file=sys.stderr)
    graph = load_graph(graph_path, graph_root, exclude_tests=not args.include_tests)
    print(_dim(f"  graph: {len(graph.nodes)} nodes · {graph_path}"), file=sys.stderr)

    if getattr(args, "symbol", None):
        nids = graph.find_nodes(args.symbol, limit=args.top)
        if not nids:
            print(f"  no graph node matches --symbol '{args.symbol}'.", file=sys.stderr)
            return (graph, graph_path, root, [], "graph (--symbol)")
        pairs = [(_synth_verdict(graph, nid), nid) for nid in nids]
        print(_dim(f"  seeded {len(pairs)} node(s) from --symbol"), file=sys.stderr)
        return graph, graph_path, root, pairs, "graph (--symbol)"

    verdicts, mode = _scan_one(args, args.query, root)
    print("", file=sys.stderr)
    seeds = [v for v in verdicts if not v.error and v.match >= args.threshold][:args.top]
    pairs = [(v, _seed_node(graph, root, v)) for v in seeds]
    return graph, graph_path, root, pairs, mode


def cmd_trace(args):
    setup = _graph_and_seeds(args)
    if isinstance(setup, int):
        return setup
    graph, graph_path, root, pairs, mode = setup
    if not pairs:
        print("  no seeds — broaden the query, lower --threshold, or check --symbol.",
              file=sys.stderr)
        return 0
    relations = [r.strip() for r in args.relations.split(",") if r.strip()]
    seed_entries = [(v, nid, graph.expand(nid, args.hops, relations, args.max_nodes) if nid else [])
                    for v, nid in pairs]
    if args.as_json:
        _trace_json(seed_entries, graph, args, graph_path, mode)
    else:
        _render_trace(seed_entries, graph, args, mode)
    return 0


def cmd_impact(args):
    setup = _graph_and_seeds(args)
    if isinstance(setup, int):
        return setup
    graph, graph_path, root, pairs, mode = setup
    if not pairs:
        print("  no seeds — broaden the query, lower --threshold, or check --symbol.",
              file=sys.stderr)
        return 0
    relations = [r.strip() for r in args.relations.split(",") if r.strip()]
    entries = [(v, nid, graph.upstream(nid, args.hops, relations, args.max_nodes) if nid else [])
               for v, nid in pairs]
    if args.as_json:
        _impact_json(entries, graph, args, graph_path, mode)
    else:
        _render_impact(entries, graph, args, mode)
    return 0


def _render_impact(entries, graph, args, mode):
    from collections import defaultdict
    C = {"dim": "\033[2m", "rst": "\033[0m", "cy": "\033[36m", "gn": "\033[32m",
         "yl": "\033[33m", "bd": "\033[1m", "rd": "\033[31m"}
    print(f"\n  {C['bd']}{C['cy']}impact:{C['rst']} {_seed_title(args)}")
    print(f"  {C['dim']}{len(entries)} seed(s) · {args.hops} hops upstream · rels {args.relations} · "
          f"{mode}{C['rst']}\n")
    for v, nid, ups in entries:
        style = C["gn"] if v.match >= 0.75 else C["yl"]
        if nid:
            label, loc = _node_loc(graph, nid)
        else:
            label, loc = _preview_label(v), f"{v.chunk.file}:{v.chunk.start_line}"
        print(f"  {style}●{C['rst']} {C['bd']}{label}{C['rst']}   {C['cy']}{loc}{C['rst']}   "
              f"{C['dim']}match {v.match:.2f}{C['rst']}" + ("" if nid else f"  {C['dim']}(not in graph){C['rst']}"))
        if not nid:
            print()
            continue
        byfile = defaultdict(list)
        for u, depth, rel, _via in ups:
            byfile[graph.nodes[u]["file"]].append((u, depth, rel))
        n_files = len(byfile)
        color = C["rd"] if len(ups) >= 12 else C["yl"] if len(ups) >= 4 else C["gn"]
        print(f"    {color}↑ {len(ups)} dependent symbol(s) across {n_files} file(s){C['rst']}")
        for f in sorted(byfile, key=lambda f: (min(d for _, d, _ in byfile[f]), -len(byfile[f]))):
            print(f"    {C['dim']}{f}{C['rst']}")
            for u, depth, rel in sorted(byfile[f], key=lambda x: x[1]):
                lab, _loc = _node_loc(graph, u)
                ln = graph.nodes[u]["line"]
                print(f"      ← {C['dim']}{rel:<7}{C['rst']} {lab}  {C['cy']}:{ln}{C['rst']}  "
                      f"{C['dim']}(hop {depth}){C['rst']}")
        print()


def _impact_json(entries, graph, args, graph_path, mode):
    seeds = []
    for v, nid, ups in entries:
        node = None
        if nid:
            n = graph.nodes[nid]
            node = {"id": nid, "label": n["label"], "file": n["file"], "line": n["line"]}
        impacts = []
        for u, depth, rel, via in ups:
            n = graph.nodes[u]
            impacts.append({"id": u, "label": n["label"], "file": n["file"], "line": n["line"],
                            "relation": rel, "depth": depth})
        files = sorted({i["file"] for i in impacts})
        seeds.append({"file": v.chunk.file, "start_line": v.chunk.start_line,
                      "end_line": v.chunk.end_line, "match": round(v.match, 3), "node": node,
                      "dependent_count": len(impacts), "files": files, "impacts": impacts})
    print(json.dumps({"query": _seed_title(args), "mode": mode, "hops": args.hops,
                      "relations": args.relations, "graph": str(graph_path), "seeds": seeds}, indent=2))


def main(argv=None):
    _force_utf8_output()
    args = _build_parser().parse_args(argv)

    if args.cmd == "serve":
        from .daemon import serve
        serve(host=args.host, port=args.port)
        return 0

    if args.cmd == "trace":
        return cmd_trace(args)

    if args.cmd == "impact":
        return cmd_impact(args)

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

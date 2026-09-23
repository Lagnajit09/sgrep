"""graphify structure provider.

sgrep finds *where* code is (semantic, per-chunk via Jev); graphify knows *how it
connects* (a call/use/method graph). `sgrep trace` uses Jev to pick precise seed nodes,
then walks graphify's edges to surface the connected flow — callers and callees — instead
of isolated hits.

We read the artifact graphify already builds (`graphify-out/graph.json`, NetworkX
node-link JSON); graphify stays the graph's owner. `--refresh` optionally rebuilds it.
"""
import json
import os
import re
import subprocess
from pathlib import Path

_LNUM = re.compile(r"L(\d+)")
# test files rarely belong in a runtime-flow trace, and graphify's inferred call
# edges often wrongly link real functions to test methods of the same name.
_TEST_RE = re.compile(r"(^|/)tests?(/|_|\.)|_test\.|\.test\.|\.spec\.|/__tests__/", re.I)
# language built-ins / stdlib names graphify records as call nodes — pure noise in a
# trace (they connect everything). Excluded from expansion.
_GENERIC = {
    "str", "int", "float", "bool", "len", "print", "exception", "dict", "list", "set",
    "tuple", "super", "isinstance", "getattr", "setattr", "hasattr", "delattr", "repr",
    "type", "format", "object", "valueerror", "keyerror", "typeerror", "runtimeerror",
    "enumerate", "range", "zip", "map", "filter", "sorted", "min", "max", "sum", "any",
    "all", "open", "bytes", "vars", "dir", "id", "hash", "next", "iter", "abs", "round",
    "console", "json", "number", "array", "promise", "date", "math", "boolean",
}


def _gnorm(s):
    return (s or "").lower().strip().rstrip("()").lstrip(".")


def find_graph(start):
    """Walk up from `start` to the nearest `graphify-out/graph.json`.

    Returns (graph_path, graph_root) — graph_root is the dir the graph's relative
    paths are rooted at (the repo where graphify ran) — or (None, None)."""
    start = Path(start).resolve()
    for d in (start, *start.parents):
        g = d / "graphify-out" / "graph.json"
        if g.exists():
            return g, d
    return None, None


def _norm(p):
    s = str(p).replace("\\", "/")
    return s.lower() if os.name == "nt" else s


class CodeGraph:
    def __init__(self, nodes, fwd, rev, by_file, excluded=None):
        self.nodes = nodes        # id -> {label, file, absfile, line, community}
        self.fwd = fwd            # id -> [(target_id, relation, weight)]
        self.rev = rev            # id -> [(source_id, relation, weight)]
        self.by_file = by_file    # normalized absfile -> sorted [(line, id)]
        self.excluded = excluded or set()  # node ids to skip (e.g. tests)

    def node_for(self, abs_path, start, end):
        """Map a chunk (abs file + line range) to the graph node it best represents:
        the symbol whose definition line falls in the chunk, else the nearest enclosing."""
        cands = self.by_file.get(_norm(abs_path))
        if not cands:
            return None
        within = [(ln, nid) for ln, nid in cands if start - 1 <= ln <= end + 1]
        if within:
            return min(within, key=lambda t: abs(t[0] - start))[1]
        below = [(ln, nid) for ln, nid in cands if ln <= end]
        if below:
            return max(below, key=lambda t: t[0])[1]
        return None

    def degree(self, nid):
        return len(self.fwd.get(nid, [])) + len(self.rev.get(nid, []))

    def find_nodes(self, symbol, limit=6):
        """Resolve a `--symbol` seed directly (no Jev). Accepts `file:line` (e.g.
        `executor.py:15`) or a name (`stream_execution`, `.authenticate`). Returns node ids
        — nearest line for file:line, exact label matches then substring for names."""
        symbol = symbol.strip()
        m = re.match(r"^(.*):(\d+)$", symbol)
        if m:
            fpart = m.group(1).replace("\\", "/").lower().lstrip("./")
            line = int(m.group(2))
            hits = []
            for nid, n in self.nodes.items():
                if nid in self.excluded or not n["file"]:
                    continue
                f = n["file"].lower()
                base = f.rsplit("/", 1)[-1]
                ok = f.endswith(fpart) if "/" in fpart else base == fpart
                if ok and abs(n["line"] - line) <= 3:
                    hits.append((abs(n["line"] - line), nid))
            return [nid for _, nid in sorted(hits)][:limit]

        def norm(s):
            return (s or "").lower().strip().rstrip("()").lstrip(".")
        want = norm(symbol)
        exact = [nid for nid, n in self.nodes.items()
                 if nid not in self.excluded and norm(n["label"]) == want]
        if exact:
            return sorted(exact, key=lambda nid: -self.degree(nid))[:limit]
        sub = [(self.degree(nid), nid) for nid, n in self.nodes.items()
               if nid not in self.excluded and want and want in (n["label"] or "").lower()]
        return [nid for _, nid in sorted(sub, reverse=True)][:limit]

    def expand(self, seed_id, hops, relations, max_nodes=24, max_fanout=8, hub_degree=18):
        """BFS from one seed along `relations` (both directions). Returns tree edges
        (from_id, to_id, relation, direction, depth); direction 'out' = seed→callee,
        'in' = caller→seed.

        Two guards keep graphify's inferred-call hubs (generic `.get()`, `api_response()`
        that link to dozens of unrelated methods) from exploding the tree: at most
        `max_fanout` highest-confidence edges per node, and we *show* a hub node but don't
        recurse through it (degree > `hub_degree`)."""
        relations = set(relations)
        seen = {seed_id}
        edges = []
        frontier = [(seed_id, 0)]
        while frontier:
            nid, depth = frontier.pop(0)
            if depth >= hops:
                continue
            cand = [(t, rel, w, "out") for (t, rel, w) in self.fwd.get(nid, []) if rel in relations]
            cand += [(s, rel, w, "in") for (s, rel, w) in self.rev.get(nid, []) if rel in relations]
            cand.sort(key=lambda x: -x[2])
            added = 0
            for other, rel, _w, direction in cand:
                if other in seen or other not in self.nodes or other in self.excluded:
                    continue
                seen.add(other)
                edges.append((nid, other, rel, direction, depth + 1))
                if self.degree(other) <= hub_degree:  # don't recurse through hubs
                    frontier.append((other, depth + 1))
                added += 1
                if added >= max_fanout or len(seen) >= max_nodes:
                    break
            if len(seen) >= max_nodes:
                break
        return edges

    def upstream(self, seed_id, hops, relations, max_nodes=60, max_fanout=14, hub_degree=40):
        """Reverse BFS — everything that (transitively) depends on `seed_id`, i.e. the
        blast radius of changing it. Returns (node_id, depth, relation, via_id), nearest
        first. A widely-used function legitimately has many callers (that's the point), so
        the hub guard is looser than trace's; it still stops pathological name-collision
        hubs from exploding."""
        relations = set(relations)
        seen = {seed_id}
        out = []
        frontier = [(seed_id, 0)]
        while frontier:
            nid, depth = frontier.pop(0)
            if depth >= hops:
                continue
            callers = sorted([(s, rel, w) for (s, rel, w) in self.rev.get(nid, []) if rel in relations],
                             key=lambda x: -x[2])
            added = 0
            for sid, rel, _w in callers:
                if sid in seen or sid not in self.nodes or sid in self.excluded:
                    continue
                seen.add(sid)
                out.append((sid, depth + 1, rel, nid))
                if self.degree(sid) <= hub_degree:
                    frontier.append((sid, depth + 1))
                added += 1
                if added >= max_fanout or len(seen) >= max_nodes:
                    break
            if len(seen) >= max_nodes:
                break
        return out


def load_graph(graph_path, graph_root, exclude_tests=True):
    d = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    root = Path(graph_root)
    nodes, by_file, excluded = {}, {}, set()
    for n in d.get("nodes", []):
        nid = n.get("id")
        if nid is None:
            continue
        sf = n.get("source_file") or ""
        rel_file = sf.replace("\\", "/")
        m = _LNUM.search(n.get("source_location") or "")
        line = int(m.group(1)) if m else 0
        absf = str((root / sf).resolve()) if sf else ""
        ftype = n.get("file_type") or "code"
        nodes[nid] = {
            "label": n.get("label") or str(nid),
            "file": rel_file,
            "absfile": absf,
            "line": line,
            "community": n.get("community"),
            "file_type": ftype,
        }
        is_test = exclude_tests and bool(_TEST_RE.search(rel_file))
        if is_test or _gnorm(n.get("label")) in _GENERIC:
            excluded.add(nid)
        # Only real code (non-test) nodes are seed-mappable; graphify's `rationale`
        # nodes are LLM notes pinned near code and would hijack seed labels.
        if absf and ftype == "code" and not is_test:
            by_file.setdefault(_norm(absf), []).append((line, nid))
    for v in by_file.values():
        v.sort()
    fwd, rev = {}, {}
    for e in d.get("links", []):
        s = e.get("source") if e.get("source") is not None else e.get("_src")
        t = e.get("target") if e.get("target") is not None else e.get("_tgt")
        if s is None or t is None:
            continue
        rel = e.get("relation") or e.get("type") or "?"
        try:
            w = float(e.get("confidence_score") or e.get("weight") or 1.0)
        except (TypeError, ValueError):
            w = 1.0
        fwd.setdefault(s, []).append((t, rel, w))
        rev.setdefault(t, []).append((s, rel, w))
    return CodeGraph(nodes, fwd, rev, by_file, excluded)


def _graphify_exe():
    from shutil import which
    return which("graphify") or str(Path.home() / ".local" / "bin" / "graphify")


def refresh(graph_root):
    """Shell out to `graphify update <root>` to rebuild the graph. (ok, message)."""
    exe = _graphify_exe()
    try:
        r = subprocess.run([exe, "update", str(graph_root)], cwd=str(graph_root),
                           capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        return False, "graphify not found (install it or drop --refresh)"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return r.returncode == 0, (tail[-1] if tail else f"exit {r.returncode}")

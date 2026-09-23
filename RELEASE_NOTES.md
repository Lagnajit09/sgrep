<!-- GitHub Release body for v0.3.0.
     Suggested title: sgrep v0.3.0 — trace & impact: follow the call graph, not just find code
     Create with:  gh release create v0.3.0 -F RELEASE_NOTES.md -t "sgrep v0.3.0 — trace & impact" -->

Until now **sgrep** could find *where* code is. v0.3.0 adds *how it connects*: two new
commands that pair Jev's semantic precision with a **[graphify](https://github.com/) call
graph** to follow callers and callees.

```bash
sgrep trace  "how is a user script dispatched to the worker" ./server
sgrep impact "the exec-worker request payload builder"        ./server
```

### ✨ What's new

- 🧭 **`sgrep trace`** — the *flow*: Jev picks the seed functions, then the graph expands
  them along **callers + callees** (`calls,method`; add `uses` with `--relations`). One
  command gives the call skeleton — cross-service edges included — so you read the hits, not
  whole files.
- 💥 **`sgrep impact`** — the *blast radius*: everything that (transitively) **depends on**
  the matched code, grouped by file with hop distance. Answers "what breaks if I change
  this?"
- 🎯 **`--symbol`** — seed `trace`/`impact` from an **exact graph node** (a name like
  `build_worker_payload` or `file:line` like `executor.py:15`) with **no Jev call**:
  instant, precise, and lets you pin each entry point for full coverage.
- 🧹 Focused output — test files, language built-ins, and generic-name hubs are excluded so
  the tree is the real flow, not a hairball (`--include-tests`, `--relations`, `--max-nodes`
  to tune). `--json` for pipelines.
- ⚡ `trace`/`impact` route their seed scan through a warm `sgrep serve` daemon when one is
  running (fail-safe), cutting the per-call latency (`trace` 1.9s → 1.0s).

### 📊 When it wins (measured)

On a **blast-radius** question over a distributed app, sgrep+graphify used **27k tokens vs
56k (plain sgrep) and 75k (manual)** — ~2–3× fewer — read **94% fewer lines**, and was the
**fastest**. Plain semantic search can't traverse a call graph, so that arm fell back to
grep. **Use `trace`/`impact` for connection/dependency questions; for "find/describe",
plain `scan` is cheaper.** Full data + caveats in [`BENCHMARK.md`](BENCHMARK.md).

### 📦 Install / upgrade
```bash
pipx install "git+https://github.com/Lagnajit09/sgrep@v0.3.0"
# upgrade an existing install:
pipx install --force "git+https://github.com/Lagnajit09/sgrep@v0.3.0"
```
`trace`/`impact` need a graphify graph: `graphify update <repo>` (or pass `--refresh`).

### ⬆️ Upgrading from 0.2.x
Fully backward compatible — `scan`/`serve` and all flags unchanged; `trace`/`impact` are new.

---
**Full changelog:** [`CHANGELOG.md`](CHANGELOG.md). 🎉

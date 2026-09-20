# Decisions

Architectural decisions for **sgrep** (semantic grep powered by Jev / TypeSafe
System One). Newest context at the bottom of each entry.

## D1 — Jev is a decision layer, not a generator
We use Jev to *judge* code, not to write text. Every query becomes typed questions
(`score` / `noul` / `choice`); Jev returns calibrated answers. This is the whole
premise: cheap, fast, typed decisions instead of LLM generation.

## D2 — v1 is standalone (no dependency on other tools)
No hard dependency on graphify, tree-sitter, or an LLM harness. sgrep walks and
chunks code itself so it runs anywhere. Structure providers plug in later (see D5).

## D3 — Fan-out, not retrieval-then-stuff; no embeddings / vector store
The "search" is a map-reduce: one Jev call per chunk, judged independently, then
ranked locally. No embedding index to build or keep in sync. Trade-off: per-query
cost scales with chunk count — mitigated by the cache (D9) and cheap Jev pricing.

## D4 — stdlib-first, httpx added only for connection pooling
Phase 1 was pure stdlib (urllib + ThreadPoolExecutor) so it runs with zero installs.
We added `httpx[http2]` **only** for pooled keep-alive connections; the client falls
back to urllib if httpx is absent. All installs live in a project `.venv`.

## D5 — Two pluggable seams
- **chunkers/** — how code is sliced (today: line windows; later: Python `ast`,
  then graphify / tree-sitter / claude-code as structure providers).
- **clients/** — who answers (today: live TypeSafe + offline mock).
This is what keeps graphify optional while still enabling `trace`/`impact` later.

## D6 — Jev question design (learned by probing the live API)
Each chunk is scored with two questions in one call:
- `relevance` (`score`) — `criteria` MUST be an **ordered list** of level anchors.
  The API returns `score` as the expected level index (0..N-1) plus `probabilities`
  + `legend`; we normalize to 0..1 by dividing by N-1.
- `match` (`noul`) — a yes/no probability, used as the primary ranking signal.
Adding the second question costs no measurable latency (both answered in one pass).

## D7 — Mock client for offline / no-access use
`--mock` returns deterministic keyword/synonym-based scores so the tool runs (and is
testable) with no API key. It is a stand-in only — the real Jev understands intent.

## D8 — HTTP/1.1 pool + warmup + jittered retry (Windows socket fix)
`WinError 10035` (WSAEWOULDBLOCK) surfaced twice with httpx **HTTP/2**: on cold start
(all threads racing one not-yet-established connection) and again under *sustained* load
(32 threads contending on HTTP/2's single multiplexed socket — 6/50 chunks failed even
after retries). Resolution, in order of importance:
1. **Use HTTP/1.1, not HTTP/2.** HTTP/1.1 uses a pool of *separate* connections, so
   there is no single-socket contention. Measured latency is identical to HTTP/2 here,
   so nothing is lost. This is the real fix (verified: 50 uncached concurrent calls, 0
   errors, ~27 req/s).
2. **Warm the connection** once before fan-out (only when there's uncached work).
3. **Jittered retry** (5x) of transient socket/transport errors only — never 4xx/5xx.
4. Default concurrency lowered to **16** to keep the connection burst reasonable.

## D9 — On-disk verdict cache
`.sgrep-cache.json`, keyed by `sha1(model + query + chunk content)`. Re-running a query
or re-scanning after small edits only pays for changed chunks; a fully-cached run is
instant. This is the main real-world latency win for iterative use.

## D10 — Name clash with XiaoConstantine/sgrep
There is an existing Go tool named `sgrep` (local llama.cpp embeddings). Our `sgrep` is a
different thing (live Jev typed decisions, no index). Decision: keep `sgrep` as the
internal working name; **rename before any public release** to avoid collision.

## D11 — Secrets
`TYPESAFE_API_KEY` lives in `.env` (gitignored). The key is loaded into the environment
at startup; it is never logged or committed. Override the endpoint via `SGREP_JEV_URL`.

## D12 — Pre-filter funnel: top-k, and it must be semantic
On a big repo, judging every chunk means one HTTPS call per chunk. The funnel runs a
local, network-free pre-filter and sends only the top-k candidates to Jev.
- **top-k, not top-p.** Top-k caps Jev calls deterministically (a cost/latency ceiling).
  Top-p (nucleus) has no ceiling and needs *calibrated* scores; local embedding scores
  aren't calibrated. Calibration belongs at the Jev stage, where we already threshold on
  the `match` probability.
- **The pre-filter is HYBRID (semantic + lexical) — each alone under-recalls, in
  opposite ways.** Semantic (Model2Vec) misses literal identifier matches that a big
  function's averaged embedding dilutes: on swiftpay the actual `p2pTransaction`
  controllers ranked #74/#313 semantically. Lexical (BM25) misses conceptual matches with
  no shared tokens: for "authentication" it dropped `session.py` and kept a `navbar`
  decoy. Fusing both via Reciprocal Rank Fusion (`prefilter/hybrid.py`) keeps each kind —
  the `p2pTransaction` controllers rank #3/#5 lexically, so RRF surfaces them.
  **Validated by an exhaustive pass:** all-2929-chunks found 7 P2P hits; the pure-semantic
  funnel found only 5 (missing the two 0.99 controllers); the hybrid funnel found all 7 —
  at ~50 Jev calls instead of 2929.
- Pluggable seam `prefilter/` (`auto`=hybrid | `hybrid` | `semantic` | `lexical` | `none`),
  engaged only when chunk count exceeds `--topk`. Model2Vec `potion-base-8M`
  (local/offline after first download) + zero-dep BM25.
- **Pre-filter caps each chunk to the first 2000 chars (`TEXT_CAP`).** Signatures and
  identifiers live at the top, so ranking barely changes, but pure-Python BM25 was
  tokenizing 23k-char average chunks (some files are minified/generated). Capping cut the
  hybrid pre-filter from ~5.2s to ~1.6s on a 2929-chunk repo with recall intact (the
  `p2pTransaction` controllers stayed at #19/#38). Jev itself still sees more (its own
  6000-char cap in `engine.py`).

## D13 — Structure-aware chunking (multi-language)
Window chunks dilute signal and give imprecise locations. We now chunk at function /
class granularity:
- `.py` via the stdlib `ast` (zero dependency).
- `.js/.jsx/.ts/.tsx/.java` via tree-sitter (`tree-sitter-language-pack`), covering
  React/Node/Express (arrow-function components like `const X = () => ...` and
  `export`-wrapped decls are handled).
- **Route/handler registrations** (`router.post("/x", h)`, `app.get(..., (req,res)=>{})`)
  are captured as precise chunks. Without this, Express route-table files (which have no
  top-level functions) silently fell back to a whole-file window chunk with a useless
  `:1` / `import ...` preview. Validated on a real payments microservices repo.
- Anything else, or any parse/dependency failure, falls back to line windows.
Large classes are split into per-method chunks (+ a header); small classes stay whole.
This also *improves pre-filter recall*: a buried instance becomes its own clean unit
instead of being averaged into a big window (see D12).

## D14 — Adaptive-k (knee), recall-first, with an exhaustive escape hatch
`prefilter/select.py` keeps the **full top-k (`--topk`) by default** and only cuts early
on a *dominant* score cliff (normalized gap >= 0.30 AND >= 4x the median gap) between
`--min-k` and `--topk`. This is top-p-style adaptivity with top-k's ceiling, but biased
hard toward recall — the pre-filter's only job (Jev decides precision downstream).

Why recall-first (learned the hard way): on a real repo, a cluster of near-duplicate
literal matches (`router.post("/on-ramp", ...)`) formed an early cliff; an aggressive
knee cut at 9 candidates and starved the actual implementations, giving 0 hits. Keeping
the full top-k restored them. `--no-adaptive` forces a hard cut. Scores are normalized
first for cliff detection (local embedding scores aren't calibrated).

**Under-recall warning (refined).** It fires only when the *first dropped* candidate
still scores well above the repo's noise floor (the median of all scores) — a "hot"
boundary where relevant code likely sits just past the cut. The earlier heuristic fired
on any flat plateau, which is nearly every large scan (it cried wolf even when the sole
true hit was found). A flat/low boundary now means we've descended into the junk tail →
no warning. When it does fire, it points at the escape hatch: `--topk 0` (exhaustive), a
larger `--topk`, or scoping the path.

## D15 — Rich UI, optional
Results render via `rich` (score+bar, colored, ellipsized) when installed, with an ANSI
plain-text fallback so the tool still works stdlib-only. Non-terminal output is widened
to 120 cols so piped/CI output stays readable. Slow phases (parsing, pre-filtering,
connecting, warming up) show a `rich` spinner in a real terminal (a plain dim line when
piped).

## D16 — Providers: TypeSafe direct is the default; Vercel is opt-in
Two providers behind a shared pooled-HTTP/1.1 base (`clients/http_base.py`); they differ
only in URL, headers, and the yes/no wire type (`boolean` on Vercel, `noul` direct —
translated per request). `--provider auto|vercel|typesafe|mock`.

**`auto` prefers TypeSafe direct.** We tried Vercel AI Gateway as primary (Jev is free
there through 2026-09-25) but its **free tier is too rate-limited for sgrep's per-chunk
fan-out** — measured: after adding a card, a 4-chunk scan took 61s with 3/4 chunks 429ing
out, and even a **2-chunk** scan failed. Auth works; throughput doesn't. So Vercel is
opt-in via `--provider vercel` (worthwhile only with higher Vercel limits). TypeSafe
direct does the same scans in ~2.6s.

Robustness added along the way: `_post` retries 429/5xx honoring `Retry-After` (up to
90s); `VercelJevClient` caps concurrency (2) and its health-check treats 429 as
"reachable, throttled" (only 401/403 trigger fallback). The HTTP/1.1 switch also made the
old connection-warmup unnecessary, so it was removed (saves a request per scan). Keys:
`VERCEL_AI_GATEWAY_API_KEY`, `TYPESAFE_API_KEY` (both in `.env`).

## D17 — Latency: prune, skip, cache
Cold scans on a real monorepo went ~9s → ~2.6s (cached query) / ~4.9s (fresh, the rest is
Jev network time). Levers:
- **os.walk with in-place dir pruning** (`discover.py`) — never descends into
  node_modules/.git/etc. `rglob` was listing 100k+ files before filtering.
- **`.sgrepignore`** (gitignore-style, loaded from scan root + cwd) + expanded default
  ignore dirs (generated, __generated__, .turbo, dist, …).
- **Auto-skip minified/generated files** (a line > 5000 chars, or binary). This alone cut
  swiftpay from 2929 → 404 chunks and the hybrid pre-filter from 5.2s → 0.04s.
- **Per-file chunk cache** (`chunkers/cache.py`, `.sgrep-chunkcache.json`) keyed by
  (mtime_ns, size) — unchanged files skip the tree-sitter parse on re-runs.
- **HF_HUB_OFFLINE** auto-set when the Model2Vec model is already cached (skips a network
  cache-check on load).
Remaining per-process floor (~2s): Model2Vec load, the Vercel health-probe, and Python
startup — a daemon/server mode would amortize these.

## D18 — Packaging: `sgrep` as an installed command
`pip install .` / `pipx install .` / `pip install -e .` expose a `sgrep` console command
(entry point `sgrep.cli:main`) via hatchling; `python -m build` produces a wheel + sdist
(verified: `sgrep_jev-0.1.0-py3-none-any.whl`). The full dependency set (httpx, rich,
model2vec, numpy, tree-sitter, tree-sitter-language-pack) ships by default so
`sgrep scan ...` works out of the box; the code still degrades gracefully if a dep is
absent. Distribution name is `sgrep-jev`; the command is `sgrep`. MIT licensed. Note:
two unrelated `sgrep` tools exist (XiaoConstantine, henrikalbihn) — rename the command
before any public/PyPI release to avoid the collision.

## D19 — Latency: batch multi-query + a warm daemon (0.2.0)
A benchmark of a Claude agent tracing a flow found sgrep won on tokens (~−45%) but *lost*
on wall-clock — the agent made 7–10 separate `sgrep` invocations, each paying Python
startup + Model2Vec load + a network round-trip. Fixing the invocation model beats
micro-optimizing a single scan:
- **Batch multi-query** (`-q`, hard cap **3**): discovery, parsing, and chunk embeddings
  are computed once and reused per query (new `PreFilter.rank_many`; semantic embeds the
  chunk matrix once and scores each query against it). All queries fan out through a
  **single** `ThreadPoolExecutor` of `--concurrency` workers (`engine.scan_many`), so peak
  in-flight Jev requests stay bounded regardless of query count — overlap without raising
  the rate. Cap is 3: a flow needs a few broad queries, not many narrow ones, and it keeps
  the fan-out rate-safe. Measured ~2.1–2.4× vs the same queries as separate runs.
- **Warm daemon** (`sgrep serve` + `--daemon`): a localhost-only, token-authed HTTP server
  (state in `~/.sgrep/daemon.json`) that keeps the process + model resident, amortizing the
  ~1–2s floor D17 flagged. The client is **fail-safe** — unreachable/errored daemon falls
  back to in-process, so `--daemon` can never break a scan, only speed it up. Serves
  `--json`; the rich report runs in-process. Measured ~1.8× per call; combined with the
  verdict cache the ladder is cold 7.9s → warm 5.3s → cached 1.1s.
- Enabler: the verdict `Cache` now takes the query per call (not at construction) so one
  cache/scan serves a batch; `render_many` renders per-query while keeping single-query
  JSON byte-identical for back-compat.
Net (Autosage re-test): the wall-clock sign flipped from slower to faster, token savings
held (~−40%). See BENCHMARK.md.

## D20 — Windows: force UTF-8 output (0.2.0 bug fix)
On Windows, piped/redirected stdout defaults to cp1252, which can't encode the score-bar
glyphs (`█ ░ ·`) — so `sgrep … > file`, `| tee`, or an agent capturing output crashed with
`UnicodeEncodeError` (the old `try/except ImportError` didn't catch it). Fix: reconfigure
stdout/stderr to UTF-8 at startup (covers rich, plain, `--json`, and the spinner) and pass
`legacy_windows=False` to the Rich console when output isn't a terminal (skips the win32
console writer). Agents use `--json` anyway, but redirect-to-file is a first-class path.

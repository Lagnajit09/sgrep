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

## D8 — Connection warmup + transient retry (Windows HTTP/2 fix)
Cold HTTP/2 + many threads on Windows raised `WinError 10035` (WSAEWOULDBLOCK), because
all requests raced one not-yet-established connection. Fix: warm the connection with a
single call before fanning out (only when there is uncached work), plus retry transient
socket/transport errors (never 4xx/5xx). This is about correctness; it does not reduce
one-shot total latency (the handshake is paid once either way).

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

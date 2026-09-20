# Changelog

All notable changes to **sgrep** are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project follows
[Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-09-20

Latency release. Understanding a flow usually takes a few related queries; this makes
that cheap. Same-quality answers as 0.1.0, but faster and with fewer tokens.

### Added
- **Batch multi-query** — pass up to **3** queries in one run with repeatable `-q`.
  Discovery, parsing, and the Model2Vec embeddings are computed **once** and reused for
  every query (`rank_many`), and all queries' Jev calls share a **single bounded pool**
  so total in-flight requests never exceed `--concurrency` (rate-limit-safe). ~2× faster
  than the same queries as separate runs.
- **Warm daemon** — `sgrep serve` keeps the process and embedding model resident;
  `sgrep scan … --daemon` uses it and skips the ~1–2s startup + model-load floor
  (~1.8× per call). Localhost-only, token-authed (`~/.sgrep/daemon.json`), and
  **fail-safe**: if no daemon is running or anything errors, it silently falls back to an
  in-process scan.
- Batch JSON output: multiple queries are wrapped in a `results` array (single-query JSON
  is unchanged).

### Fixed
- **Windows crash on piped/redirected output** — `sgrep … > file` / `| tee` (and any
  agent capturing output) hit a cp1252 `UnicodeEncodeError` on the score-bar glyphs.
  stdout/stderr are now reconfigured to UTF-8 and Rich no longer takes the legacy-Windows
  console path when output isn't a terminal.

### Changed
- The verdict cache takes the query per call (not at construction), so one cache serves a
  whole batch.

### Measured (Autosage; agent tracing a flow, manual vs sgrep)
- Tokens −40%, code read into context −66% at equal answer quality; wall-clock went from
  *slower* (0.1.0, many cold calls) to *faster* with batch + daemon. Latency ladder for a
  3-query batch: cold 7.9s → warm daemon 5.3s → cached 1.1s. See `BENCHMARK.md`.

[0.2.0]: https://github.com/Lm09/sgrep-jev/releases/tag/v0.2.0

## [0.1.0] — 2026-09-20

Initial release — semantic grep for your codebase, powered by **Jev** (TypeSafe System
One). Ask questions in plain English; sgrep asks Jev one typed question per code chunk
and ranks the hits by calibrated confidence.

### Added
- **Semantic `scan`** — natural-language code search that matches by meaning, not
  keywords (`verify_jwt()` matches "authentication" without the word).
- **Structure-aware chunking** — function/class units via Python `ast` and tree-sitter
  for JS/JSX/TS/TSX/Java (incl. React components, arrow functions, and Express route
  handlers); line-window fallback. Hits point at the real `file:line`.
- **Hybrid pre-filter funnel** — a local, offline pass (Model2Vec + BM25, fused via
  Reciprocal Rank Fusion) with adaptive top-k, so large repos send ~50 calls instead of
  thousands. `--prefilter` and `--topk` / `--min-k` to tune it.
- **Providers** — TypeSafe direct (default) and Vercel AI Gateway (`--provider vercel`),
  with 429/5xx retry that honors `Retry-After`.
- **Caching** — on-disk chunk cache and verdict cache for near-instant re-scans.
- **`.sgrepignore`** support plus automatic skipping of minified/generated files and
  common vendor/build directories.
- **Rich terminal UI** with score bars and `file:line`, plus `--json` output.
- **Offline `--mock`** mode (no API key required).
- Packaged as the `sgrep` command (`pipx install .` / `pip install -e .`).

### Known limitations
- Requires a Jev API key. The Vercel free tier is heavily rate-limited (tiny scans only).
- Pre-filter recall is generous but not guaranteed — use `--topk 0` for an exhaustive
  (every-chunk) scan when completeness matters.
- A ~2s per-invocation floor from model load, cold connection, and Python startup.

[0.1.0]: https://github.com/Lm09/sgrep-jev/releases/tag/v0.1.0

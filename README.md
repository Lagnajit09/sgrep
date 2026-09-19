# sgrep — semantic grep, powered by Jev

Ask your codebase questions in plain English:

```bash
sgrep scan "which code handles authentication" ./src
```

Instead of matching keywords (grep) or building a vector index (embeddings),
`sgrep` slices your repo into chunks and asks **Jev** (TypeSafe's System One model)
one cheap typed question per chunk: *"does this match the query?"* — then ranks the
answers by calibrated confidence.

Because a Jev call is ~150 tokens, ~100ms, and output is free, scanning a whole
repo costs pennies — cheap enough to run in CI on every PR.

## How it works

```
walk repo -> chunk locally -> one Jev call per chunk (parallel) -> rank -> report
   (free)       (free)              (~$0.03 / 5k functions)         (free)
```

- **No embeddings, no vector store, no index to keep in sync.**
- **Meaning, not keywords:** `verify_jwt()` scores high for "authentication" even
  without the word; a comment that merely mentions a term won't false-positive.
- **Scattering is irrelevant:** every chunk is judged independently, so matches are
  found no matter how many files/dirs they're spread across.

## Try it now (no setup)

```bash
# Offline mock Jev — runs anywhere, no API key:
python -m sgrep scan "which code handles authentication" sample_repo --mock

# Live Jev — reads TYPESAFE_API_KEY from .env:
python -m sgrep scan "which code handles authentication" sample_repo
```

## Options

| flag | meaning |
|------|---------|
| `--threshold 0.6` | minimum match probability to show |
| `--top 20` | max hits |
| `--window 60 --overlap 10` | chunk sizing (lines) |
| `--ext .py` | restrict to extensions (repeatable) |
| `--include / --exclude` | glob filters (repeatable) |
| `--prefilter auto` | local pre-filter before Jev: `auto`/`semantic` (Model2Vec) · `lexical` · `none` |
| `--topk 50` | max candidates sent to Jev after the pre-filter (0 = no cap / exhaustive) |
| `--min-k 8` | adaptive floor: always keep at least this many candidates |
| `--no-adaptive` | hard top-k cut instead of adaptive knee detection |
| `--mock` | offline stand-in for Jev |
| `--json` | machine-readable output |

## Structure-aware chunking

Files are chunked at **function / class** granularity, not arbitrary line blocks:
`.py` via the stdlib `ast`; `.js/.jsx/.ts/.tsx/.java` via tree-sitter (covers React
components, arrow functions, `export`-wrapped decls); line windows for everything else.
This gives precise `file:line` hits and better pre-filter recall.

## The funnel (big repos)

Judging every chunk means one HTTPS call per chunk. When a repo has more than `--topk`
chunks, `sgrep` first runs a **local, offline pre-filter** (Model2Vec static embeddings)
and sends only the **top-k** candidates to Jev — turning "1000 calls" into "50 calls".
The pre-filter is semantic on purpose: a keyword filter would drop the very code Jev is
best at finding (e.g. `verify_jwt` for "authentication"). Install with the extra:

```bash
pip install -e ".[all]"   # semantic pre-filter + tree-sitter parsers + rich UI
# or pick extras: .[semantic]  .[parsers]  .[ui]  (each degrades gracefully if absent)
```

## Config

`sgrep` loads `TYPESAFE_API_KEY` from a local `.env` (gitignored). Point at a
gateway with the same `{model, state, questions}` shape via `SGREP_JEV_URL`.

## Roadmap

- **Phase 1 (now):** standalone semantic `scan` over line-window chunks.
- **Phase 2:** precise Python function/class chunks via stdlib `ast`.
- **Phase 3:** pluggable structure providers (graphify / tree-sitter / claude-code)
  behind the chunker seam — unlocks `sgrep trace` (semantic taint across the call
  graph) and `sgrep impact` (blast-radius).

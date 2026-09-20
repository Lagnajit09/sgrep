<!-- GitHub Release body for v0.2.0.
     Suggested title: sgrep v0.2.0 — batch queries + a warm daemon (same answers, faster & cheaper)
     Create with:  gh release create v0.2.0 -F RELEASE_NOTES.md -t "sgrep v0.2.0 — batch queries + a warm daemon" -->

**sgrep** turns natural-language questions into precise code locations by asking **Jev**
(TypeSafe's System One model) one cheap *typed* question per code chunk. Tracing a flow
usually takes a few related questions — **0.2.0 makes that cheap**: same-quality answers as
0.1.0, but faster and with fewer tokens.

```bash
sgrep scan "how are requests authenticated" ./src \
  -q "where are tokens verified" \
  -q "service-to-service auth" --json
```

### ✨ What's new

- 🎯 **Batch multi-query (`-q`, up to 3)** — one run answers several questions. Discovery,
  parsing, and the local embeddings are computed **once** and reused for every query, and
  all the Jev calls share a **single bounded pool** so total in-flight requests never
  exceed `--concurrency` (rate-limit-safe). ~2× faster than the same queries run
  separately. The cap is 3 on purpose: a flow needs a few *broad* queries, not many.
- ⚡ **Warm daemon (`sgrep serve` + `--daemon`)** — keep the process and embedding model
  resident so each scan skips the ~1–2s startup + model-load floor (~1.8× per call).
  Localhost-only, token-authed, and **fail-safe** — if no daemon is running (or anything
  errors) it silently falls back to an in-process scan, so it can only speed a scan up,
  never break one.
- 🪟 **Fixed a Windows crash** on piped/redirected output (`sgrep … > file`, `| tee`, or
  any agent capturing stdout) — a cp1252 `UnicodeEncodeError` on the score-bar glyphs.
  Output is now UTF-8 safe everywhere.

### 📊 Measured (Autosage; a Claude agent tracing a real flow, manual vs sgrep)

| | Manual | sgrep (batch + daemon) |
|---|--:|--:|
| Total tokens | 121,010 | **72,761 (−40%)** |
| Code read into context | 4,170 lines | **1,421 (−66%)** |
| Wall-clock | 225s | **209s (faster, was slower in 0.1.0)** |

Latency ladder for one 3-query batch: **cold 7.9s → warm daemon 5.3s → cached 1.1s**.
Full methodology and caveats in [`BENCHMARK.md`](BENCHMARK.md).

### 🚀 Usage

```bash
# batch: trace a flow in one shot
sgrep scan "..." ./src -q "..." -q "..." --json

# warm daemon: start once, then every --daemon scan is warm
sgrep serve                                  # Ctrl-C to stop
sgrep scan "..." ./src --json --daemon
```

### 📦 Install
```bash
pipx install .          # global, isolated `sgrep` command
# or
pip install -e .        # into the current environment
```
Requires **Python 3.10+**. First run downloads a tiny (~7 MB) local embedding model.

### ⬆️ Upgrading from 0.1.0
Fully backward compatible — existing commands and single-query `--json` output are
unchanged. `-q`, `--daemon`, and `sgrep serve` are additive.

### ⚠️ Known limitations
- Needs a Jev API key (TypeSafe). The Vercel free tier is heavily rate-limited.
- Pre-filter recall is generous but not guaranteed; use `--topk 0` for an exhaustive scan.
- Benchmark numbers are n=1 per cell — directional, not ±1%.

### What's next
Structure-aware `sgrep trace` / `sgrep impact` via a graphify-backed provider — one call
returning a call-graph slice instead of several queries.

---
**Full changelog:** see [`CHANGELOG.md`](CHANGELOG.md). 🎉

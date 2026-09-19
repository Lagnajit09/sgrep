# Flow

How a `sgrep scan "<query>" <path>` runs, end to end.

```
 ┌─────────────┐   ┌──────────┐   ┌──────────────┐   ┌───────────────────────┐   ┌────────┐   ┌────────┐
 │  discover   │──▶│  chunk   │──▶│ build 1 Q-set │──▶│  fan-out: 1 call/chunk │──▶│  rank  │──▶│ render │
 │ (local,free)│   │(local,   │   │  from query   │   │  (parallel, cached)    │   │(local) │   │        │
 └─────────────┘   │  free)   │   └──────────────┘   └───────────────────────┘   └────────┘   └────────┘
                   └──────────┘                                 │
                                                        ┌───────┴────────┐
                                                        │ Jev (TypeSafe) │
                                                        └────────────────┘
```

## Stages

1. **discover** — `discover.py` walks `<path>`, skips ignored dirs (`.git`,
   `node_modules`, `.venv`, …), filters by extension / `--include` / `--exclude`,
   and drops files over ~1 MB. Local, no network.

2. **chunk** — `chunkers/window.py` slices each file into overlapping line windows
   (default 60 lines, 10 overlap), each tagged `file:start-end`. This is the seam
   where graphify / `ast` / tree-sitter plug in later. Local, no network.

2b. **pre-filter (funnel, optional)** — when chunk count exceeds `--topk`, `prefilter/`
    scores every chunk locally (Model2Vec cosine, or BM25 fallback) and keeps the top-k.
    Turns "one HTTPS call per chunk" into "one per surviving candidate". Local, no
    network. Must be semantic — see DECISIONS.md D12.

3. **build questions** — `engine.build_questions(query)` turns the query into ONE
   reusable question set (built once, reused for every chunk):
   ```jsonc
   {
     "relevance": { "type": "score", "instructions": "...match the intent: '<query>'?",
                    "criteria": ["unrelated", "loosely related", "directly implements"] },
     "match":     { "type": "noul",  "instructions": "Does this code ...: '<query>'?" }
   }
   ```

4. **fan-out** — `engine.scan()` checks the cache for each chunk; misses are sent to
   Jev in parallel (`ThreadPoolExecutor`, default 32, pooled HTTP/2). Each call:
   ```jsonc
   POST https://api.typesafe.ai/v1/systemone
   { "model": "jev-latest", "state": "<one chunk>", "questions": <the set above> }
   ```
   Response (normalized in `clients/typesafe.py`):
   ```jsonc
   { "answers": {
       "relevance": { "score": 1.74, "confidence": 0.6,
                      "probabilities": {"0":0.02,"1":0.23,"2":0.75} },  // -> 1.74/2 = 0.87
       "match":     { "noul": 0.96 } } }
   ```
   Verdicts are written back to the cache.

5. **rank** — `Verdict.rank_key = 0.6*match + 0.4*score`; filter by `--threshold`
   (on `match`), sort, take `--top`. Local.

6. **render** — `render.py` prints a ripgrep-style list (score bar, `file:line`, a
   signature-preview line) or `--json`.

## Data shapes
- `Chunk(file, start_line, end_line, text)`
- `Verdict(chunk, score, match, confidence, error)` — `score`/`match` are 0..1.

## Latency profile (measured, from India → TypeSafe US)
- Single Jev call: **~0.35–0.4s** (network RTT dominates; Jev itself is fast).
- Parallel fan-out: **~20 chunks / 0.7s**, ~40 / 0.9s (throughput ~30–47 req/s).
- One-shot small scan: ~1s of that is **fixed** connection + Python startup.
- **Cached re-run: ~0.00s** (no calls).
- Levers used: parallelism, pooled HTTP/2 keep-alive, connection warmup, disk cache.
  Future: local pre-filter funnel, a persistent/daemon mode to amortize the connection.

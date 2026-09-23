---
name: sgrep
description: >-
  Semantic code search and call-graph analysis via the `sgrep` CLI (powered by Jev).
  Use it to FIND or LOCATE code by meaning rather than keywords (e.g. "where is
  authentication handled", "which code builds the worker payload"), to TRACE a flow
  along callers and callees, or to find the IMPACT / blast radius / dependents of a
  function before changing it. Prefer this over grep for "where/how does X work?" and
  "what breaks if I change Y?" questions across a repo. Runs `sgrep scan`, `sgrep trace`,
  and `sgrep impact`. Requires the `sgrep` CLI installed separately (pipx).
allowed-tools: Bash
---

# sgrep — semantic code search & call-graph analysis

`sgrep` asks a typed model (Jev) one "does this match?" question per code chunk and ranks
hits by calibrated confidence, so it matches by **meaning**, not keywords (`verify_jwt()`
scores high for "authentication" without the word). With a
[graphify](https://github.com/Lagnajit09/sgrep) call graph it also follows callers and
callees. Use it instead of grep-and-read to locate and connect code with far fewer tokens.

## When to reach for it

| The user is asking… | Command |
| --- | --- |
| "where is / which code handles / find the code that…" (locate by meaning) | `sgrep scan` |
| "how does A reach B / trace this flow / show callers and callees" | `sgrep trace` |
| "what depends on X / what breaks if I change X / blast radius / impact" | `sgrep impact` |

**Skip sgrep** when an exact string or symbol grep is enough, or when the relevant file is
already open/known — a direct `grep`/read is cheaper there. sgrep earns its cost on
*meaning* ("where/how does X work") and *connection* ("what breaks if I change Y") questions.

## First-use check

Before the first sgrep call in a session, confirm the CLI is installed:

```bash
sgrep --version
```

If that fails, tell the user to install it (see **Setup** at the bottom) and stop — don't
fall back to guessing. `trace`/`impact` additionally need a graphify graph
(`graphify-out/graph.json`) at or above the scan path.

## scan — where is the code?

```bash
sgrep scan "which code handles authentication" <path> --json
```

- `<path>` defaults to `.`. Always pass `--json` when you'll parse the results.
- Read the returned `file:line` hits directly — open only the specific hits, not whole files.
- **Batch up to 3 related queries** in one run (shares one discovery + parse + model load,
  rate-limit-safe). Use this to trace a topic with a few *broad* queries rather than many runs:

```bash
sgrep scan "how are requests authenticated" <path> \
  -q "where are JWT tokens verified" \
  -q "service-to-service auth between backend services" --json
```

- **Faster repeats:** start a warm daemon once (`sgrep serve`, runs in the background), then
  add `--daemon` to each scan to skip the ~1–2s startup + model-load. `--daemon` is
  fail-safe — it silently runs in-process if no daemon is up, and it serves `--json`.

## trace — how does it connect? (the flow)

```bash
sgrep trace "how is a user script dispatched to the worker" <path> --json
```

Jev picks seed functions, then the call graph expands them along **callers + callees**
(`calls,method` by default; add more with `--relations calls,method,uses`). Returns the
call skeleton — cross-service edges included — so you read the hits, not whole files.

- **`--symbol`** seeds from an *exact* graph node with **no Jev call** (instant, precise) —
  a name or `file:line`. Use it when you already know the entry point:

```bash
sgrep trace --symbol stream_execution <path> --json
sgrep trace --symbol executor.py:15 <path> --json
```

## impact — what breaks if I change this? (blast radius)

```bash
sgrep impact "the function that verifies JWTs" <path> --json
sgrep impact --symbol build_worker_payload <path> --json
```

Everything that (transitively) **depends on** the matched code, grouped by file with hop
distance. This is the command for "safe to change?" / "who calls this?" questions.

## Picking the right command (this matters for cost)

- Using `trace`/`impact` for a plain *find* wastes tokens — the call-graph edges are extra
  to read. Use `scan`.
- Using `scan` for a *dependency* question misses the graph and forces a fallback to grep.
  Use `trace`/`impact`.

In one benchmark on a distributed app, `impact` answered a blast-radius question with
**~2–3× fewer tokens** (and faster) than either manual grep or plain `scan`.

## Useful flags

- `--json` — machine-readable output (use it whenever you'll parse results).
- `--top N` — max hits (`scan`) or seed nodes (`trace`/`impact`).
- `--threshold 0.6` — minimum match probability to keep.
- `--hops`, `--max-nodes`, `--relations` — tune call-graph expansion (`trace`/`impact`).
- `--include-tests` — keep test files (excluded by default).
- `--refresh` — rebuild the graphify graph first (`graphify update`) if it's stale.
- `--ext .py`, `--include`, `--exclude` — narrow the file set (`scan`).

## Setup (the user does this once)

- **Install the CLI:** `pipx install "git+https://github.com/Lagnajit09/sgrep"` (command is `sgrep`).
- **API key:** set `TYPESAFE_API_KEY` as a real environment variable, or in a global
  `~/.config/sgrep/.env` (`%APPDATA%\sgrep\.env` on Windows). The scanned repo's own `.env`
  is never read. Or run offline with `--mock` (no key, stubbed results).
- **Graph for `trace`/`impact`:** build it with `graphify update <repo>` (or pass `--refresh`).

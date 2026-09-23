# sgrep benchmark — does semantic search cut what Claude has to read?

**Question.** When Claude explores an unfamiliar codebase to *understand a flow*, does using **sgrep** (semantic locate-then-read) cost fewer tokens than the normal grep-and-read loop — for the same-quality answer?

**Short answer.** Yes for tokens: **~41% fewer total tokens** and **~68% less code read into context** across three domains, at **equal answer quality**. No for wall-clock: sgrep was **~18% slower** end-to-end (it trades model reasoning time — unchanged — for extra network round-trips to Jev). The token win grows with how *scattered* the relevant code is.

> Baseline round (pre-graphify), measured 2026-09-20.

## Setup

- **Target:** "Autosage" — a distributed app (FastAPI `autobot` agent service, `exec-worker` Python worker, Django `server` control-plane, React/TS `client`). ~450 source files across 4 services.
- **Method:** for each domain, two subagents solve the **identical** deliverable (summary → entry points → step-by-step → cross-service handoff → key files, all cited by `file:line`).
  - **Manual (baseline):** grep / glob / read only. No sgrep.
  - **sgrep:** sgrep as the locator; reads only the `file:line` hits it returns.
- **Controls:** same model (Claude Sonnet) on both arms; same rules; **read-only** (no repo edits); secrets never opened. Both answers were checked to converge on the same real flow before comparing cost (quality parity).
- **Primary metric:** total tokens the subagent consumed (harness-reported, input+output). Secondary: code lines / content pulled into context (self-reported ledger).

## Results — total Claude tokens (authoritative)

| Domain | Manual | sgrep | Reduction |
|---|--:|--:|--:|
| Authentication | 76,679 | 39,020 | **−49%** |
| Script-execution | 81,498 | 44,797 | **−45%** |
| Trigger-node config | 62,730 | 47,038 | **−25%** |
| **Total** | **220,907** | **130,855** | **−41%** |

## Results — code read into context

| Domain | Manual lines | sgrep lines | Reduction |
|---|--:|--:|--:|
| Authentication | 2,990 | 672 | −78% |
| Script-execution | 4,361 | 918 | −79% |
| Trigger-node config | 1,856 | 1,343 | −28% |
| **Total** | **9,207** | **2,933** | **−68%** |

Files opened: 34 → 27. Content-into-context (est. tokens): 109,349 → 37,747 (−66%).

## Results — wall-clock (the honest caveat)

| Domain | Manual | sgrep |
|---|--:|--:|
| Authentication | 244s | 237s |
| Script-execution | 132s | 166s |
| Trigger-node config | 103s | 163s |
| **Total** | **479s** | **566s (+18%)** |

sgrep did **not** speed up understanding here. Wall-clock is dominated by the model's own reasoning/writing (identical on both arms); sgrep *adds* ~2–3s per Jev query and each agent ran 7–10 queries. sgrep offloads the *search*, not the *thinking*.

## What the numbers say

- **The win is tokens, not latency (yet).** ~41% fewer tokens for the same answer = ~41% cheaper, plus far more context headroom — Claude can hold a bigger system in mind before hitting limits or suffering attention dilution.
- **The advantage scales with scatter.** Auth logic spans 3 services + the client → biggest win (−49%). Trigger config lives in one tidy `triggers/` Django app that's easy to grep → smallest win (−25%). The messier and more distributed the code, the more sgrep helps. (On a repo far larger than one service, the manual baseline's read cost balloons while sgrep's locate cost stays roughly bounded.)
- **The reduction is conservative.** The sgrep arm carried a fixed "how to use sgrep" brief in its prompt (extra input tokens) and still came out ~41% ahead.

## Round 2 — after batch + warm daemon (the latency fixes)

Round 1 showed sgrep won on tokens but *lost* on wall-clock, because the agent made
7–10 separate cold `sgrep` invocations (each paying Python startup + model load +
network). We added **batch multi-query** (≤3 queries share one startup/parse/embedding)
and a **warm daemon** (resident model), then re-ran two domains — one large, one small —
with the sgrep agent using both. Same-session manual baseline each time.

| Domain (size) | Metric | Manual | sgrep (batch+daemon) | Δ |
|---|---|--:|--:|--:|
| **script-execution** (large) | total tokens | 64,505 | 34,746 | **−46%** |
| | lines read | 2,246 | 603 | −73% |
| | wall-clock | 109s | 105s | **−4s** |
| **trigger-config** (small) | total tokens | 56,505 | 38,015 | **−33%** |
| | lines read | 1,924 | 818 | −57% |
| | wall-clock | 116s | 104s | **−12s** |

The sgrep agent used **2 batch calls (6 queries)** per domain instead of 7–10 separate
calls. **The wall-clock sign flipped** vs Round 1:

| Domain | Round 1 (separate calls) | Round 2 (batch + daemon) |
|---|--:|--:|
| script-execution | **+34s slower** | **−4s faster** |
| trigger-config | **+60s slower** | **−12s faster** |

Token savings held (~40% combined; 121,010 → 72,761), lines read −66% (4,170 → 1,421),
and wall-clock went from a loss to a modest win — while both arms produced the same-quality,
same-`file:line` answer.

### Latency ladder (3-query batch on `execution_engine`)

| | Time | vs cold |
|---|--:|--:|
| Cold, in-process (startup + model load + parse + Jev) | 7.87s | — |
| Warm daemon (no startup/model/parse; full Jev) | 5.31s | −33% |
| Cached (warm daemon + verdict-cache hits) | 1.11s | −86% |

The daemon removes the ~2.5s fixed floor; the verdict cache removes the Jev fan-out on
re-scans.

## Round 3 — graphify (`trace`/`impact`), by question type

Phase 3 added `sgrep trace`/`impact` (Jev seeds + a graphify call graph). The value depends
entirely on the *kind* of question. Same target (Autosage), same-model agents, same deliverable.

**Describe-the-flow question** ("explain the script-execution flow end to end") — a linear
narrative:

| | Manual | Raw sgrep | sgrep+graphify |
|---|--:|--:|--:|
| Total tokens | 64,505 | 34,746 | 44,548 |
| Wall-clock | 109s | 105s | 175s |

Here graphify **lost** to raw sgrep: the key functions are semantically findable, so the
call-graph edges are extra tokens to read, and `trace` wasn't daemon-accelerated yet
(4 trace calls × model-load → slow).

**Connection / blast-radius question** ("map everything affected if we change how Django
dispatches to the exec-worker"):

| | Manual | Raw sgrep (+grep) | sgrep+graphify |
|---|--:|--:|--:|
| Total tokens | 75,460 | 55,954 | **27,061** |
| Lines read | 3,246 | 1,948 | **181** |
| Files read | 8 | 8 | **4** |
| Wall-clock | 146s | 247s | **94s** |
| Locators | grep-heavy | 2 scan **+ 8 grep** | 1 impact + 1 trace + 1 scan |
| Completeness (fns) | 18 | 19 | 13 |

Here graphify **won decisively** on cost (−52% vs raw sgrep, −64% vs manual) and speed
(fastest) — the graph hands over the connections instead of making the agent read files to
reconstruct them. The raw-sgrep agent abandoned scan-only and fell back to grep 8× (semantic
search can't traverse a call graph), becoming the slowest arm. **Caveat:** graphify
under-covered (13 vs 18–19) — a single seed (`stream_execution`) missed the parallel *inline*
workflow dispatch (`tasks.py:794`) and its callers; seeding all entry points closes the gap.

**Takeaway:** use `trace`/`impact` for **connection/dependency/blast-radius** questions
(where they're 2–3× cheaper and faster), not for "find/describe" (where plain `scan` already
wins). `trace`/`impact` now route their seed scan through the warm daemon, removing the
per-call model-load tax.

## Threats to validity

- **n = 1 per cell.** One run per domain per arm; agent exploration is stochastic. Treat as directional, not ±1%.
- **Token metric** is the harness's per-subagent total; "lines/content read" is a self-reported estimate and only approximate.
- Single repo, single model (Sonnet). Absolute numbers will shift with model and codebase.

## Next: the graphify round

We expect the wall-clock picture to flip once sgrep can return structure (Phase 3, graphify-backed `trace` / `impact`): one call yielding a call-graph slice replaces many query→read round-trips, and a daemon mode removes the ~2s cold-start per invocation. Same protocol, same three domains — numbers to be appended here.

_Reproduce: `sgrep scan "<question>" <path> --json`. Raw per-run ledgers are in the benchmark record._

import statistics


def adaptive_select(scores_desc, min_k, max_k):
    """Choose how many top candidates to keep, adaptively and recall-first.

    Given descending-sorted pre-filter scores, KEEP THE FULL top-k (`max_k`) unless
    there is a *dominant* score cliff between `min_k` and `max_k` — only then cut
    early. The pre-filter's only job is recall (Jev decides precision downstream),
    so we bias hard toward keeping candidates. A common trap: a cluster of near-
    duplicate high scorers (e.g. literal `router.post("/on-ramp", ...)` lines) forms
    an early cliff that would otherwise cut off the real implementations.

    Scores are normalized first because local embedding scores are compressed and
    uncalibrated. Returns (kept_count, warn, reason).
    """
    n = len(scores_desc)
    if n == 0:
        return 0, False, ""
    hi = min(max_k, n)
    if n <= min_k:
        return n, False, ""
    lo = max(1, min(min_k, hi))

    smin, smax = scores_desc[-1], scores_desc[0]
    rng = (smax - smin) or 1e-9
    norm = [(s - smin) / rng for s in scores_desc]

    gaps = [(norm[i - 1] - norm[i], i) for i in range(lo, hi)]
    if not gaps:
        return hi, False, ""
    best_gap, best_i = max(gaps)
    median_gap = statistics.median(g for g, _ in gaps) or 1e-9

    # Only cut early on a clear, dominant cliff; otherwise keep the whole top-k.
    if best_gap >= 0.30 and best_gap >= 4 * median_gap:
        kept = best_i
    else:
        kept = hi

    # Under-recall warning: fire only when the FIRST DROPPED candidate still scores
    # well above the repo's noise floor (the median of all scores) — i.e. we're cutting
    # while scores are still clearly relevant. A flat/low boundary means we've already
    # descended into the junk tail, so nothing relevant is beyond the cut (no warning).
    warn, reason = False, ""
    if kept >= hi and hi < n:
        top = scores_desc[0]
        median = statistics.median(scores_desc)
        boundary = scores_desc[hi]  # the first dropped candidate
        spread = top - median
        if spread > 1e-9 and (boundary - median) > 0.5 * spread:
            warn = True
            reason = (f"the candidate just past top-{hi} still scores well above the "
                      f"median — relevant code may be beyond the cut")
    return kept, warn, reason

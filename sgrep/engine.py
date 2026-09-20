from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import Verdict


def build_questions(query: str) -> dict:
    """Turn a natural-language query into a reusable Jev question set (built once)."""
    return {
        "relevance": {
            "type": "score",
            "instructions": f"How strongly does this code match the intent: '{query}'?",
            # `score` questions require `criteria`: an ordered list of level anchors.
            # The API returns the expected level index; the client normalizes to 0..1.
            "criteria": [
                "unrelated to the query",
                "loosely related to the query",
                "directly implements or handles the query",
            ],
        },
        "match": {
            "type": "noul",
            "instructions": f"Does this code implement or directly handle: '{query}'?",
        },
    }


def _judge(client, chunk, questions):
    """One Jev call for one chunk. Never raises — errors ride along on the Verdict."""
    # Cap the payload so a stray huge chunk can't balloon token cost / latency.
    state = (
        f"# file: {chunk.file} (lines {chunk.start_line}-{chunk.end_line})\n"
        f"{chunk.text[:6000]}"
    )
    try:
        ans = client.judge(state, questions)
        rel = ans.get("relevance", {})
        m = ans.get("match", {})
        return Verdict(
            chunk=chunk,
            score=float(rel.get("value") or 0.0),
            match=float(m.get("value") or 0.0),
            confidence=rel.get("confidence") or m.get("confidence"),
        )
    except Exception as e:  # one bad chunk shouldn't sink the whole scan
        return Verdict(chunk=chunk, error=str(e))


def scan(chunks, client, query, concurrency=25, progress=None, cache=None):
    """Fan out one Jev call per chunk (in parallel) and collect verdicts.

    Chunks already in `cache` (same model + query + content) are served without
    an API call, so re-running a query or re-scanning after small edits is
    near-free.
    """
    return scan_many([(query, chunks)], client, concurrency=concurrency,
                     progress=progress, cache=cache)[0]


def scan_many(jobs, client, concurrency=25, progress=None, cache=None):
    """Fan out several queries over their candidate chunks in ONE bounded pool.

    `jobs` is a list of `(query, chunks)`. Returns a list of verdict-lists aligned
    to `jobs`. All queries share a single ThreadPoolExecutor of `concurrency`
    workers, so total in-flight Jev requests never exceed `concurrency` no matter
    how many queries there are — the network side stays rate-limit-safe while the
    per-query fan-outs overlap (instead of running strictly back-to-back).
    """
    results = [[] for _ in jobs]
    questions = [build_questions(q) for q, _ in jobs]
    todo = []  # (job_index, chunk)

    for ji, (query, chunks) in enumerate(jobs):
        for chunk in chunks:
            cached = cache.get(query, chunk) if cache else None
            if cached is not None:
                results[ji].append(Verdict(
                    chunk=chunk,
                    score=cached.get("score", 0.0),
                    match=cached.get("match", 0.0),
                    confidence=cached.get("confidence"),
                ))
            else:
                todo.append((ji, chunk))

    total = len(todo)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(_judge, client, chunk, questions[ji]): ji
                   for ji, chunk in todo}
        for done, fut in enumerate(as_completed(futures), start=1):
            ji = futures[fut]
            v = fut.result()
            results[ji].append(v)
            if cache is not None and not v.error:
                cache.put(jobs[ji][0], v.chunk,
                          {"score": v.score, "match": v.match, "confidence": v.confidence})
            if progress:
                progress(done, total)
    return results

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


def scan(chunks, client, query, concurrency=25, progress=None, cache=None):
    """Fan out one Jev call per chunk (in parallel) and collect verdicts.

    Chunks already in `cache` (same model + query + content) are served without
    an API call, so re-running a query or re-scanning after small edits is
    near-free.
    """
    questions = build_questions(query)
    verdicts = []
    todo = []

    for chunk in chunks:
        cached = cache.get(chunk) if cache else None
        if cached is not None:
            verdicts.append(
                Verdict(
                    chunk=chunk,
                    score=cached.get("score", 0.0),
                    match=cached.get("match", 0.0),
                    confidence=cached.get("confidence"),
                )
            )
        else:
            todo.append(chunk)

    def work(chunk):
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

    total = len(todo)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = [ex.submit(work, c) for c in todo]
        for done, fut in enumerate(as_completed(futures), start=1):
            v = fut.result()
            verdicts.append(v)
            if cache is not None and not v.error:
                cache.put(v.chunk, {"score": v.score, "match": v.match, "confidence": v.confidence})
            if progress:
                progress(done, total)
    return verdicts

import json
import re

_SIG = re.compile(r"\b(def|class|func|function|fn|public|private|export|async|interface|struct|impl)\b")


def _preview(text):
    """Pick the most representative line of a chunk: a signature if present."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    for ln in lines:
        if _SIG.search(ln):
            return ln.strip()
    return lines[0].strip()


def _bar(v, width=20):
    filled = int(round(max(0.0, min(1.0, v)) * width))
    return "█" * filled + "░" * (width - filled)


def _color(v):
    if v >= 0.75:
        return "\033[32m"   # green
    if v >= 0.5:
        return "\033[33m"   # yellow
    return "\033[90m"       # grey


_RESET = "\033[0m"


def render(verdicts, query, threshold=0.6, top=20, as_json=False, mode="", elapsed=None):
    ranked = sorted(verdicts, key=lambda v: v.rank_key, reverse=True)
    hits = [v for v in ranked if not v.error and v.match >= threshold]
    errors = [v for v in ranked if v.error]

    if as_json:
        payload = {
            "query": query,
            "threshold": threshold,
            "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
            "chunks_scanned": len(verdicts),
            "hits": [
                {
                    "file": v.chunk.file,
                    "start_line": v.chunk.start_line,
                    "end_line": v.chunk.end_line,
                    "match": round(v.match, 3),
                    "score": round(v.score, 3),
                    "confidence": v.confidence,
                }
                for v in hits[:top]
            ],
        }
        print(json.dumps(payload, indent=2))
        return

    timing = f" in {elapsed:.2f}s" if elapsed is not None else ""
    print(
        f'\n  sgrep "{query}"'
        f'   [{mode}]   threshold {threshold:.2f}   '
        f"{len(hits)} hit(s) / {len(verdicts)} chunks{timing}\n"
    )
    if not hits:
        print("  no matches above threshold.\n")
    for v in hits[:top]:
        first = _preview(v.chunk.text)
        if len(first) > 72:
            first = first[:69] + "..."
        c = _color(v.match)
        print(f"  {c}{v.match:0.2f}{_RESET} {_bar(v.match)}  {v.chunk.file}:{v.chunk.start_line}")
        print(f"       {first}")
    if errors:
        print(f"\n  \033[31m{len(errors)} chunk(s) errored\033[0m  e.g. {errors[0].error[:100]}")
    print()

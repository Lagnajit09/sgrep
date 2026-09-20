import json
import re

_SIG = re.compile(r"\b(def|class|func|function|fn|public|private|export|async|interface|struct|impl)\b")
_BOILERPLATE = ("#", "//", "import ", "from ", "export default", "export {",
                "export *", "module.exports", "package ", "@")


def _preview(text):
    """Most representative line of a chunk: prefer a signature; skip import/export
    boilerplate and comments so window-fallback chunks don't preview as `import ...`."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    pool = [ln for ln in lines if not ln.lstrip().startswith(_BOILERPLATE)]
    pool = pool or [ln for ln in lines if not ln.lstrip().startswith(("#", "//"))] or lines
    for ln in pool:
        if _SIG.search(ln):
            return ln.strip()
    return pool[0].strip()


def _loc(file, line):
    """Compact location that always keeps the filename + line visible."""
    parts = file.split("/")
    short = file if len(parts) <= 2 else f"{parts[0]}/…/{parts[-1]}"
    return f"{short}:{line}"


def _style(v):
    return "green" if v >= 0.75 else "yellow" if v >= 0.5 else "grey58"


def _bar(v, width=10):
    filled = int(round(max(0.0, min(1.0, v)) * width))
    return "█" * filled + "░" * (width - filled)


_LOC_W = 40


def _result_dict(verdicts, query, threshold, top, elapsed):
    ranked = sorted(verdicts, key=lambda v: v.rank_key, reverse=True)
    hits = [v for v in ranked if not v.error and v.match >= threshold]
    return {
        "query": query, "threshold": threshold,
        "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
        "chunks_scanned": len(verdicts),
        "hits": [{"file": v.chunk.file, "start_line": v.chunk.start_line,
                  "end_line": v.chunk.end_line, "match": round(v.match, 3),
                  "score": round(v.score, 3), "confidence": v.confidence}
                 for v in hits[:top]],
    }


def render(verdicts, query, threshold=0.6, top=20, as_json=False, mode="", elapsed=None):
    if as_json:
        print(json.dumps(_result_dict(verdicts, query, threshold, top, elapsed), indent=2))
        return

    ranked = sorted(verdicts, key=lambda v: v.rank_key, reverse=True)
    hits = [v for v in ranked if not v.error and v.match >= threshold]
    errors = [v for v in ranked if v.error]
    try:
        _render_rich(hits, errors, verdicts, query, threshold, top, mode, elapsed)
    except ImportError:
        _render_plain(hits, errors, verdicts, query, threshold, top, mode, elapsed)


def render_many(results, queries, threshold=0.6, top=20, as_json=False, mode="", elapsed=None):
    """Render several query result-sets. `results[i]` are the verdicts for `queries[i]`.

    A single query renders exactly like `render()` (same JSON shape too), so batch
    mode is a superset — multi-query JSON wraps the per-query dicts in `results`.
    """
    if len(results) == 1:
        render(results[0], queries[0], threshold=threshold, top=top,
               as_json=as_json, mode=mode, elapsed=elapsed)
        return

    if as_json:
        print(json.dumps({
            "queries": list(queries),
            "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
            "results": [_result_dict(v, q, threshold, top, None)
                        for v, q in zip(results, queries)],
        }, indent=2))
        return

    for verdicts, query in zip(results, queries):
        render(verdicts, query, threshold=threshold, top=top,
               as_json=False, mode=mode, elapsed=None)


def _fit_loc(loc):
    return loc if len(loc) <= _LOC_W else "…" + loc[-(_LOC_W - 1):]


def _render_rich(hits, errors, verdicts, query, threshold, top, mode, elapsed):
    from rich.console import Console
    from rich.markup import escape

    console = Console()
    if not console.is_terminal:  # piped / captured: comfortable fixed room, and
        # skip the legacy-Windows renderer (it writes via the cp1252 win32 console).
        console = Console(width=118, legacy_windows=False)
    avail = max(24, console.width - (2 + 2 + 1 + 15 + 2 + _LOC_W + 2))

    console.print()
    console.rule(f"[bold cyan]{escape(query)}[/bold cyan]", style="cyan", align="left")
    sub = f"{len(hits)} match(es) in {len(verdicts)} chunks · {mode} · threshold {threshold:.2f}"
    if elapsed is not None:
        sub += f" · {elapsed:.2f}s"
    console.print(f"[dim]{sub}[/dim]\n")

    if not hits:
        console.print("  [yellow]no matches above threshold.[/yellow]\n")
    for i, v in enumerate(hits[:top], 1):
        st = _style(v.match)
        loc = _fit_loc(_loc(v.chunk.file, v.chunk.start_line))
        preview = _preview(v.chunk.text)
        if len(preview) > avail:
            preview = preview[:avail - 1] + "…"
        console.print(
            f"  [dim]{i:>2}[/dim] [{st}]{v.match:.2f} {_bar(v.match)}[/{st}]  "
            f"[cyan]{escape(loc):<{_LOC_W}}[/cyan]  [dim]{escape(preview)}[/dim]"
        )
    if errors:
        console.print(f"\n  [red]{len(errors)} errored[/red] [dim]· {escape(errors[0].error[:80])}[/dim]")
    console.print()


def _render_plain(hits, errors, verdicts, query, threshold, top, mode, elapsed):
    reset, dim = "\033[0m", "\033[2m"
    color = {"green": "\033[32m", "yellow": "\033[33m", "grey58": "\033[90m"}
    timing = f" · {elapsed:.2f}s" if elapsed is not None else ""
    print(f"\n  \033[1;36m{query}{reset}")
    print(f"  {dim}{len(hits)} match(es) in {len(verdicts)} chunks · {mode} · "
          f"threshold {threshold:.2f}{timing}{reset}\n")
    if not hits:
        print("  no matches above threshold.\n")
    for i, v in enumerate(hits[:top], 1):
        c = color[_style(v.match)]
        loc = _fit_loc(_loc(v.chunk.file, v.chunk.start_line))
        print(f"  {i:>2} {c}{v.match:0.2f} {_bar(v.match)}{reset}  \033[36m{loc}{reset}")
        print(f"       {dim}{_preview(v.chunk.text)}{reset}")
    if errors:
        print(f"\n  \033[31m{len(errors)} errored{reset} · {errors[0].error[:80]}")
    print()

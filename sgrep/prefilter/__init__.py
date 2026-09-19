import sys


def get_prefilter(kind="auto"):
    """Return a PreFilter instance, or None for 'none'.

    'auto'/'hybrid' fuse semantic (Model2Vec) + lexical (BM25) via RRF — the most
    robust for recall. 'semantic' or 'lexical' force a single filter. Anything that
    needs Model2Vec falls back to lexical if it's unavailable.
    """
    kind = (kind or "auto").lower()
    if kind == "none":
        return None

    if kind in ("auto", "hybrid"):
        try:
            from .hybrid import HybridPreFilter
            return HybridPreFilter()
        except Exception as e:  # noqa: BLE001 -- model2vec missing / download failed
            print(f"  (hybrid pre-filter unavailable: {e}; using lexical)", file=sys.stderr)
            from .lexical import LexicalPreFilter
            return LexicalPreFilter()

    if kind == "semantic":
        try:
            from .semantic import SemanticPreFilter
            return SemanticPreFilter()
        except Exception as e:  # noqa: BLE001
            print(f"  (semantic pre-filter unavailable: {e}; using lexical)", file=sys.stderr)

    from .lexical import LexicalPreFilter
    return LexicalPreFilter()

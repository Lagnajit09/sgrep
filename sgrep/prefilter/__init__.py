import sys


def get_prefilter(kind="auto"):
    """Return a PreFilter instance, or None for 'none'.

    'auto'/'semantic' try Model2Vec and fall back to lexical if it's unavailable.
    'lexical' forces the zero-dep BM25 filter. 'none' disables pre-filtering.
    """
    kind = (kind or "auto").lower()
    if kind == "none":
        return None

    if kind in ("auto", "semantic"):
        try:
            from .semantic import SemanticPreFilter

            return SemanticPreFilter()
        except Exception as e:  # noqa: BLE001 -- model2vec missing / download failed
            print(
                f"  (semantic pre-filter unavailable: {e}; using lexical)",
                file=sys.stderr,
            )

    from .lexical import LexicalPreFilter

    return LexicalPreFilter()

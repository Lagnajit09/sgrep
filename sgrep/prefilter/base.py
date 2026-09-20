# The pre-filter only needs each chunk's signature + head to rank it. Capping the
# text here keeps BM25 tokenization (pure Python) and embedding fast even when some
# files produce huge chunks (minified/generated code). Jev still sees more (its own cap).
TEXT_CAP = 2000


class PreFilter:
    """Local, network-free scorer used to shrink the candidate set before Jev.

    `rank` returns one score per chunk (higher = more relevant). It only needs to
    be good enough for *recall* — Jev makes the precise, calibrated decision on the
    top-k survivors. See DECISIONS.md D12 (top-k, not top-p).
    """

    name = "base"

    def rank(self, query, chunks):
        raise NotImplementedError

    def rank_many(self, queries, chunks):
        """Score `chunks` for each query, returning one score-list per query.

        Default re-ranks per query; subclasses override to compute the (expensive)
        chunk-side work — embeddings, BM25 doc stats — ONCE and reuse it for every
        query. That's the whole point of batch mode.
        """
        return [self.rank(q, chunks) for q in queries]

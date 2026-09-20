from .base import PreFilter


def reciprocal_rank_fusion(score_lists, k=60):
    """Fuse several score lists by rank (RRF): fused_i = sum 1/(k + rank_i).

    Rank-based, so it needs no score calibration across the two very different
    scales (cosine similarity vs BM25). A chunk that ranks high in *either* filter
    surfaces in the fused top-k.
    """
    n = len(score_lists[0]) if score_lists else 0
    fused = [0.0] * n
    for scores in score_lists:
        order = sorted(range(n), key=lambda i: scores[i], reverse=True)
        for rank, idx in enumerate(order, 1):
            fused[idx] += 1.0 / (k + rank)
    return fused


class HybridPreFilter(PreFilter):
    """Fuse semantic (Model2Vec) + lexical (BM25) rankings via RRF.

    Semantic catches conceptual matches; lexical catches literal identifier matches
    that a large function's averaged embedding dilutes. Measured on swiftpay: the
    actual `p2pTransaction` controllers ranked #74/#313 semantically but #3/#5
    lexically — fusion keeps both in the top-k.
    """

    def __init__(self):
        from .lexical import LexicalPreFilter
        from .semantic import SemanticPreFilter
        self.sem = SemanticPreFilter()
        self.lex = LexicalPreFilter()
        self.name = f"hybrid({self.sem.name.split(':')[-1]}+bm25)"

    def rank(self, query, chunks):
        return reciprocal_rank_fusion([
            self.sem.rank(query, chunks),
            self.lex.rank(query, chunks),
        ])

    def rank_many(self, queries, chunks):
        # Each side does its chunk-work once; fuse per query.
        sem = self.sem.rank_many(queries, chunks)
        lex = self.lex.rank_many(queries, chunks)
        return [reciprocal_rank_fusion([sem[i], lex[i]]) for i in range(len(queries))]

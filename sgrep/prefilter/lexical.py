import math
import re
from collections import Counter

from .base import TEXT_CAP, PreFilter

_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def _tokens(s):
    return [t.lower() for t in _TOK.findall(s)]


class LexicalPreFilter(PreFilter):
    """Zero-dependency BM25 pre-filter.

    Fast and offline, but keyword-based: it will miss chunks that are semantically
    relevant without sharing tokens (e.g. `verify_jwt` for "authentication"). Use
    it only as a fallback when the semantic pre-filter is unavailable.
    """

    name = "lexical"

    def _index(self, chunks):
        """Tokenize + BM25 doc statistics — the query-independent work."""
        docs = [_tokens(c.text[:TEXT_CAP]) for c in chunks]
        n = len(docs)
        df = Counter()
        for d in docs:
            df.update(set(d))
        tfs = [Counter(d) for d in docs]
        dls = [len(d) for d in docs]
        avgdl = (sum(dls) / n) if n else 0.0
        return n, df, tfs, dls, avgdl

    def _score(self, query, index):
        n, df, tfs, dls, avgdl = index
        if n == 0:
            return []
        q = _tokens(query)
        k1, b = 1.5, 0.75
        scores = []
        for tf, dl in zip(tfs, dls):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * (tf[t] * (k1 + 1)) / (tf[t] + k1 * (1 - b + b * dl / avgdl))
            scores.append(s)
        return scores

    def rank(self, query, chunks):
        return self._score(query, self._index(chunks))

    def rank_many(self, queries, chunks):
        index = self._index(chunks)  # built once, reused per query
        return [self._score(q, index) for q in queries]

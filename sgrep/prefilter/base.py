class PreFilter:
    """Local, network-free scorer used to shrink the candidate set before Jev.

    `rank` returns one score per chunk (higher = more relevant). It only needs to
    be good enough for *recall* — Jev makes the precise, calibrated decision on the
    top-k survivors. See DECISIONS.md D12 (top-k, not top-p).
    """

    name = "base"

    def rank(self, query, chunks):
        raise NotImplementedError

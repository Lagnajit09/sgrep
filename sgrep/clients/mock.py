import hashlib
import re

from .base import JevClient

# Words in the query itself that carry no signal.
_STOP = {
    "the", "a", "an", "is", "are", "code", "this", "that", "does", "do", "which",
    "what", "handle", "handling", "handles", "implement", "implements", "contain",
    "contains", "on", "to", "of", "in", "for", "and", "or", "with", "how",
    "strongly", "match", "matches", "scale", "identity", "etc", "related",
    "following", "directly", "intent",
}

# Tiny synonym expansion so the OFFLINE mock produces believable rankings.
# The real Jev needs none of this — it understands intent directly.
_SYNONYMS = {
    "authentication": [
        "auth", "authenticate", "login", "logout", "signin", "signup", "session",
        "token", "jwt", "credential", "credentials", "password", "passwd", "oauth",
        "bearer", "verify", "principal", "cookie", "user",
    ],
    "money": [
        "payment", "pay", "charge", "refund", "invoice", "billing", "price",
        "amount", "currency", "stripe", "transaction", "checkout", "subscription",
    ],
    "database": [
        "db", "sql", "query", "insert", "select", "update", "delete", "orm",
        "postgres", "mysql", "mongo", "sqlite", "cursor", "commit",
    ],
}


def _criteria_text(crit):
    if isinstance(crit, dict):
        return " ".join(crit.keys())
    if isinstance(crit, list):
        parts = []
        for c in crit:
            parts.append(c if isinstance(c, str) else " ".join(str(v) for v in c.values()))
        return " ".join(parts)
    return ""


def _keywords(questions):
    words = set()
    for q in questions.values():
        text = q.get("instructions", "") + " " + _criteria_text(q.get("criteria"))
        for w in re.findall(r"[a-zA-Z_]{3,}", text.lower()):
            if w not in _STOP:
                words.add(w)
    expanded = set(words)
    for w in list(words):
        expanded.update(_SYNONYMS.get(w, []))
    return expanded


def _base_score(state, keywords):
    if not keywords:
        return 0.0
    s = state.lower()
    hits = sum(1 for kw in keywords if kw in s)
    occurrences = sum(s.count(kw) for kw in keywords)
    density = occurrences / (len(s.split()) + 1)
    return min(1.0, hits / 3.0) * 0.7 + min(1.0, density * 40) * 0.3


class MockClient(JevClient):
    """Deterministic offline stand-in for Jev, so sgrep runs with no API access."""

    def judge(self, state, questions):
        base = _base_score(state, _keywords(questions))
        jitter = (int(hashlib.sha1(state.encode()).hexdigest(), 16) % 1000 / 1000.0 - 0.5) * 0.08
        val = max(0.0, min(1.0, base + jitter))
        out = {}
        for name, q in questions.items():
            kind = q.get("type", "noul")
            out[name] = {
                "kind": kind,
                "value": val,
                "confidence": abs(val - 0.5) * 2 if kind == "score" else None,
            }
        return out

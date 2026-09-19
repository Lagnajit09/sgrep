import json
import os

from .http_base import HttpJevClient

VERCEL_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
_HEADERS = {
    "ai-model-id": "typesafe-ai/jev",
    "ai-evaluation-model-specification-version": "4",
    "ai-gateway-protocol-version": "0.0.1",
}


def _to_vercel(questions):
    """Vercel's wire format names the yes/no question type `boolean` (the direct API
    calls it `noul`); score/choice are identical. Translate on the way out."""
    out = {}
    for name, q in questions.items():
        qq = dict(q)
        if qq.get("type") == "noul":
            qq["type"] = "boolean"
        out[name] = qq
    return out


class VercelJevClient(HttpJevClient):
    """Live Jev via the Vercel AI Gateway evaluation-model endpoint.

    Primary provider (free for Jev through 2026-09-25). Requires a valid card on the
    Vercel account to unlock the free credits — otherwise the gateway returns HTTP 403
    and the caller falls back to the TypeSafe direct client.
    """

    # Free-tier Jev on the gateway is heavily rate-limited; keep concurrency tiny and let
    # the Retry-After backoff in HttpJevClient smooth out the rest.
    MAX_CONCURRENCY = 2

    def __init__(self, api_key=None, pool=16, timeout=30):
        api_key = api_key or os.environ.get("VERCEL_AI_GATEWAY_API_KEY")
        if not api_key:
            raise RuntimeError("no VERCEL_AI_GATEWAY_API_KEY found")
        super().__init__(api_key, pool=min(pool, self.MAX_CONCURRENCY), timeout=timeout, extra_headers=_HEADERS)
        self.max_concurrency = self.MAX_CONCURRENCY

    def healthcheck(self):
        """One probe (no retry). 429 counts as available — it means the endpoint works
        but is throttled; the per-call retry handles that. Only auth/permission errors
        (401/403) trigger fallback to TypeSafe."""
        payload = json.dumps(
            {"state": "ok", "questions": {"_": {"type": "boolean", "instructions": "ready?"}}}
        ).encode("utf-8")
        try:
            status, _, body = self._send(VERCEL_URL, payload)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        if status in (200, 429):
            return True, None
        return False, f"HTTP {status}: {body[:80].decode('utf-8', 'replace')}"

    def judge(self, state, questions):
        data = self._post(VERCEL_URL, {"state": state, "questions": _to_vercel(questions)})
        return self._normalize(data, "boolean")

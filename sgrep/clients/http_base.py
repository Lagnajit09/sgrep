import json
import random
import time
import urllib.error
import urllib.request

from .base import JevClient

try:
    import httpx
except ImportError:  # keep a stdlib path working without the venv
    httpx = None

_RETRYABLE = {429, 500, 502, 503, 504}


class HttpJevClient(JevClient):
    """Shared HTTP plumbing for live Jev clients.

    Pooled HTTP/1.1 keep-alive (robust with a thread pool on Windows; HTTP/2's single
    socket raced under load) + jittered retry of transient socket errors. Subclasses
    provide the URL, any extra headers, the request body, and the boolean field name.
    """

    def __init__(self, api_key, pool=16, timeout=30, extra_headers=None):
        if not api_key:
            raise RuntimeError("missing API key")
        self.api_key = api_key
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        if extra_headers:
            self._headers.update(extra_headers)
        self._client = None
        if httpx is not None:
            limits = httpx.Limits(max_connections=pool, max_keepalive_connections=pool)
            self._client = httpx.Client(timeout=timeout, limits=limits, headers=self._headers)

    @property
    def transport(self):
        return "httpx/1.1" if self._client is not None else "urllib"

    def _send(self, url, data):
        """One request. Returns (status, headers, body_bytes); raises on transport failure."""
        if self._client is not None:
            resp = self._client.post(url, content=data)
            return resp.status_code, resp.headers, resp.content
        req = urllib.request.Request(url, data=data, method="POST", headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:  # 4xx/5xx come back as HTTPError
            return e.code, e.headers, e.read()

    def _post(self, url, payload):
        data = json.dumps(payload).encode("utf-8")
        last = None
        for attempt in range(6):
            try:
                status, headers, body = self._send(url, data)
            except Exception as e:  # noqa: BLE001 -- transport/URLError, retry
                last = f"transport: {e}"
                time.sleep(0.4 * (2 ** attempt) + random.random() * 0.3)
                continue
            if status == 200:
                return json.loads(body)
            if status in _RETRYABLE:  # rate limit / transient server error -> back off
                last = f"HTTP {status}"
                ra = headers.get("retry-after") or headers.get("Retry-After")
                if ra:
                    try:
                        delay = min(float(ra), 90)  # honor the server's rate-limit window
                    except (TypeError, ValueError):
                        delay = min(0.5 * (2 ** attempt), 12)
                else:
                    delay = min(0.5 * (2 ** attempt), 12)
                time.sleep(delay + random.random() * 0.3)
                continue
            raise RuntimeError(f"HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
        raise RuntimeError(f"rate-limited/unavailable after retries ({last})")

    def _healthcheck(self, url, payload):
        """One probe call. Returns (ok, error_str)."""
        try:
            self._post(url, payload)
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def close(self):
        if self._client is not None:
            self._client.close()

    @staticmethod
    def _norm_number(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, v))

    @classmethod
    def _norm_score(cls, ans):
        # score = expected level index (0..N-1); normalize to 0..1 by the level count.
        levels = ans.get("legend") or ans.get("probabilities") or {}
        n = len(levels)
        try:
            raw = float(ans.get("score") or 0.0)
        except (TypeError, ValueError):
            raw = 0.0
        return max(0.0, min(1.0, raw / (n - 1) if n >= 2 else raw))

    def _normalize(self, data, bool_key):
        """Normalize a Jev response into {name: {kind, value, confidence}}.

        `bool_key` is the wire name for the yes/no answer ("noul" direct, "boolean"
        via Vercel). Handles both an `answers` wrapper and a flat top-level map.
        """
        answers = {}
        if isinstance(data, dict):
            answers = data.get("answers")
            if not isinstance(answers, dict):
                answers = {k: v for k, v in data.items() if k not in ("usage", "model")}
        out = {}
        for name, ans in answers.items():
            if not isinstance(ans, dict):
                continue
            conf = ans.get("confidence")
            if bool_key in ans or "noul" in ans or "boolean" in ans:
                val = ans.get(bool_key, ans.get("noul", ans.get("boolean")))
                out[name] = {"kind": "noul", "value": self._norm_number(val), "confidence": conf}
            elif "score" in ans:
                out[name] = {"kind": "score", "value": self._norm_score(ans), "confidence": conf}
            elif "choice" in ans:
                out[name] = {"kind": "choice", "value": ans.get("choice"), "confidence": conf,
                             "probabilities": ans.get("probabilities")}
        return out

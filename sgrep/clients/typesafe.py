import json
import os
import time
import urllib.error
import urllib.request

from .base import JevClient

try:
    import httpx
except ImportError:  # keep the stdlib path working without the venv
    httpx = None

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"


class TypeSafeClient(JevClient):
    """Live Jev via the native TypeSafe System One endpoint.

    Uses a pooled httpx client (HTTP/2 keep-alive) when available so the whole
    scan shares connections instead of paying a TLS handshake per chunk. Falls
    back to urllib (one connection per call) if httpx isn't installed.
    """

    def __init__(self, api_key=None, model="jev-latest", url=None, timeout=30, pool=32):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("no TYPESAFE_API_KEY found (set it in .env or the environment)")
        self.model = model
        self.url = url or os.environ.get("SGREP_JEV_URL", DEFAULT_URL)
        self.timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        self._client = None
        self._http2 = False
        if httpx is not None:
            limits = httpx.Limits(max_connections=pool, max_keepalive_connections=pool)
            try:
                self._client = httpx.Client(
                    timeout=timeout, limits=limits, http2=True, headers=self._headers
                )
                self._http2 = True
            except Exception:  # http2 needs the h2 package; degrade to HTTP/1.1 keep-alive
                self._client = httpx.Client(timeout=timeout, limits=limits, headers=self._headers)

    @property
    def transport(self):
        if self._client is None:
            return "urllib"
        return "httpx/2" if self._http2 else "httpx/1.1"

    def judge(self, state, questions):
        payload = {"model": self.model, "state": state, "questions": questions}
        if self._client is None:
            return self._normalize(self._urllib_post(payload))

        # Retry transient socket/transport errors (e.g. WinError 10035 when many
        # threads race a cold HTTP/2 connection) — but never retry HTTP 4xx/5xx.
        last = None
        for attempt in range(3):
            try:
                resp = self._client.post(self.url, json=payload)
            except Exception as e:  # noqa: BLE001 -- transport error, retry
                last = e
                time.sleep(0.1 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return self._normalize(resp.json())
        raise RuntimeError(f"transport error after retries: {last}")

    def _urllib_post(self, payload):
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), method="POST", headers=self._headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"network error: {e.reason}") from None

    def warmup(self):
        """Establish the pooled connection with one tiny call.

        With HTTP/2 the whole scan shares one connection, so the first request
        otherwise pays a TLS+H2 handshake that blocks every parallel call behind
        it. Warming once up front makes the fan-out ~3x faster (measured).
        """
        if self._client is None:
            return
        try:
            self._client.post(
                self.url,
                json={
                    "model": self.model,
                    "state": "ok",
                    "questions": {"_": {"type": "noul", "instructions": "ready?"}},
                },
            )
        except Exception:  # noqa: BLE001 -- warmup is best-effort
            pass

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

    def _normalize(self, data):
        answers = data.get("answers", {}) if isinstance(data, dict) else {}
        out = {}
        for name, ans in answers.items():
            if not isinstance(ans, dict):
                continue
            conf = ans.get("confidence")
            if "noul" in ans:
                out[name] = {"kind": "noul", "value": self._norm_number(ans["noul"]), "confidence": conf}
            elif "score" in ans:
                # `score` is the expected level index (0..N-1); normalize to 0..1
                # using the number of levels reported in `legend`/`probabilities`.
                levels = ans.get("legend") or ans.get("probabilities") or {}
                n = len(levels)
                try:
                    raw = float(ans.get("score") or 0.0)
                except (TypeError, ValueError):
                    raw = 0.0
                value = raw / (n - 1) if n >= 2 else raw
                out[name] = {"kind": "score", "value": max(0.0, min(1.0, value)), "confidence": conf}
            elif "choice" in ans:
                out[name] = {
                    "kind": "choice",
                    "value": ans.get("choice"),
                    "confidence": conf,
                    "probabilities": ans.get("probabilities"),
                }
        return out

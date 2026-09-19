import os

from .http_base import HttpJevClient

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"


class TypeSafeClient(HttpJevClient):
    """Live Jev via the native TypeSafe System One endpoint (uses the `noul` wire type).

    Override the endpoint with SGREP_JEV_URL. Reads TYPESAFE_API_KEY from the env.
    """

    def __init__(self, api_key=None, model="jev-latest", url=None, pool=16, timeout=30):
        api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise RuntimeError("no TYPESAFE_API_KEY found (set it in .env or the environment)")
        super().__init__(api_key, pool=pool, timeout=timeout)
        self.model = model
        self.url = url or os.environ.get("SGREP_JEV_URL", DEFAULT_URL)

    def _payload(self, state, questions):
        return {"model": self.model, "state": state, "questions": questions}

    def warmup(self):
        self._healthcheck(self.url, self._payload("ok", {"_": {"type": "noul", "instructions": "ready?"}}))

    def judge(self, state, questions):
        return self._normalize(self._post(self.url, self._payload(state, questions)), "noul")

import os
from pathlib import Path

# Quiet the HuggingFace cache-check chatter (the "Fetching/Download" bars).
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# If the model is already cached, skip HF's network cache-check for a faster load.
# (Gated on cache existence so the very first download still works online.)
_hf_hub = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"
if (_hf_hub / "models--minishlab--potion-base-8M").exists():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

from .base import TEXT_CAP, PreFilter

DEFAULT_MODEL = "minishlab/potion-base-8M"


class SemanticPreFilter(PreFilter):
    """Model2Vec static-embedding pre-filter (local, offline after first download).

    Same family sgrep.sh uses: tiny int8 static embeddings, cosine similarity, no
    per-token neural inference. Preserves semantic recall (unlike a keyword filter)
    while staying fast enough to run on every chunk locally.
    """

    def __init__(self, model_name=DEFAULT_MODEL):
        from model2vec import StaticModel  # imported lazily so it's an optional dep
        import numpy as np

        self._np = np
        self.model = StaticModel.from_pretrained(model_name)
        self.name = f"semantic:{model_name.split('/')[-1]}"

    def rank(self, query, chunks):
        np = self._np
        emb = np.asarray(self.model.encode([c.text[:TEXT_CAP] for c in chunks]), dtype=np.float32)
        q = np.asarray(self.model.encode([query])[0], dtype=np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8
        q /= np.linalg.norm(q) + 1e-8
        return (emb @ q).tolist()

from dataclasses import dataclass
from typing import Optional


@dataclass
class Chunk:
    """A unit of code judged independently by Jev."""

    file: str  # repo-relative, posix-style
    start_line: int
    end_line: int
    text: str

    @property
    def id(self) -> str:
        return f"{self.file}:{self.start_line}-{self.end_line}"


@dataclass
class Verdict:
    """Jev's judgment of a single chunk against the query."""

    chunk: Chunk
    score: float = 0.0        # 0..1 relevance (from the `score` question)
    match: float = 0.0        # 0..1 probability (from the `noul` question)
    confidence: Optional[float] = None
    error: Optional[str] = None

    @property
    def rank_key(self) -> float:
        # `match` is the primary signal; `score` breaks ties / sharpens ordering.
        return 0.6 * self.match + 0.4 * self.score

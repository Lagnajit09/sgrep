from abc import ABC, abstractmethod


class JevClient(ABC):
    """A source of typed decisions.

    `judge` takes a `state` (the code chunk) and a `questions` dict in TypeSafe
    shape, and returns a normalized dict:

        {name: {"kind": "score"|"noul"|"choice",
                "value": float|str,
                "confidence": float|None}}
    """

    @abstractmethod
    def judge(self, state: str, questions: dict) -> dict:
        ...

    def close(self) -> None:
        pass

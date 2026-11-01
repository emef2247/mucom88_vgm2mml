"""Neutral complete-unit signatures consumed by source loop planning."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    kind: str
    key: tuple

    def signature(self):
        return self.key

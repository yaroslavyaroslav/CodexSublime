"""Prompt-history state that is independent from a plugin backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PromptHistorySession:
    """Track up/down navigation while preserving the user's current draft."""

    index: Optional[int] = None
    draft: str = ""

    @property
    def browsing(self) -> bool:
        return self.index is not None

    def reset(self) -> None:
        self.index = None
        self.draft = ""

    def previous(self, history: List[str], current_text: str) -> Optional[str]:
        if not history:
            return None

        if self.index is None:
            self.draft = current_text
            self.index = len(history) - 1
        elif self.index > 0:
            self.index -= 1

        return history[self.index]

    def next(self, history: List[str]) -> Optional[str]:
        if self.index is None:
            return None

        if self.index < len(history) - 1:
            self.index += 1
            return history[self.index]

        draft = self.draft
        self.reset()
        return draft


"""Codex input-panel persistence and prompt-history navigation."""

from __future__ import annotations

from typing import Any, ClassVar

from .vendor.sublime_chat_ui.history import PromptHistorySession


class CodexInputHistoryController:
    """Adapt the shared history state machine to Sublime window settings."""

    PANEL_NAME = 'codex_input'
    DRAFT_STORAGE_KEY = 'CODEX_INPUT_DRAFT_STORAGE'
    HISTORY_STORAGE_KEY = 'CODEX_INPUT_HISTORY_STORAGE'

    _history_sessions: ClassVar[dict[int, PromptHistorySession]] = {}
    _hiding_windows: ClassVar[set[int]] = set()

    @classmethod
    def is_input_panel_view(cls, view: Any | None) -> bool:
        if view is None:
            return False
        window = view.window()
        if window is None:
            return False
        panel = window.find_output_panel(cls.PANEL_NAME)
        return panel is not None and panel.id() == view.id()

    @classmethod
    def get_draft(cls, window: Any) -> str:
        draft = window.settings().get(cls.DRAFT_STORAGE_KEY, '')
        return draft if isinstance(draft, str) else ''

    @classmethod
    def save_draft(cls, window: Any, draft: str) -> None:
        window.settings().set(cls.DRAFT_STORAGE_KEY, draft)

    @classmethod
    def clear_draft(cls, window: Any) -> None:
        window.settings().erase(cls.DRAFT_STORAGE_KEY)

    @classmethod
    def get_history(cls, window: Any) -> list[str]:
        history = window.settings().get(cls.HISTORY_STORAGE_KEY, [])
        if not isinstance(history, list):
            return []
        return [item for item in history if isinstance(item, str) and item]

    @classmethod
    def record_history(cls, window: Any, prompt: str) -> None:
        history = cls.get_history(window)
        history.append(prompt)
        window.settings().set(cls.HISTORY_STORAGE_KEY, history)

    @classmethod
    def history_session(cls, window: Any) -> PromptHistorySession:
        return cls._history_sessions.setdefault(window.id(), PromptHistorySession())

    @classmethod
    def reset_history_session(cls, window: Any) -> None:
        cls._history_sessions.pop(window.id(), None)

    @classmethod
    def reset_if_history_entry_changed(cls, window: Any, current_text: str) -> bool:
        """Leave browsing mode when the recalled entry was edited in place."""

        session = cls.history_session(window)
        if session.index is None:
            return False

        history = cls.get_history(window)
        if session.index < len(history) and history[session.index] == current_text:
            return False

        cls.reset_history_session(window)
        return True

    @classmethod
    def hide_panel(cls, window: Any) -> None:
        """Hide the panel without routing the command back through cancellation."""

        cls._hiding_windows.add(window.id())
        try:
            window.run_command('hide_panel')
        finally:
            cls._hiding_windows.discard(window.id())

    @classmethod
    def is_hiding_panel(cls, window: Any) -> bool:
        return window.id() in cls._hiding_windows

    @staticmethod
    def is_caret_at_history_boundary(panel: Any) -> bool:
        selections = list(panel.sel())
        if len(selections) != 1 or not selections[0].empty():
            return False
        return panel.rowcol(selections[0].begin()) == (0, 0)

    @classmethod
    def should_navigate_previous(cls, panel: Any) -> bool:
        window = panel.window()
        return bool(window and cls.is_caret_at_history_boundary(panel) and cls.get_history(window))

    @classmethod
    def should_navigate_next(cls, panel: Any) -> bool:
        window = panel.window()
        return bool(
            window and cls.is_caret_at_history_boundary(panel) and cls.history_session(window).browsing
        )

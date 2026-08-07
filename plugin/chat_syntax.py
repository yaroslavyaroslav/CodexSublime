"""Migrate persisted Codex chat views to the shared Markdown syntax."""

from __future__ import annotations

from typing import Any

from .vendor.sublime_chat_ui.presentation import syntax_resource


TRANSCRIPT_VIEW_FLAG = "codex_is_transcript"
LEGACY_CHAT_SYNTAXES = frozenset(
    {
        "Packages/Codex/Syntaxes/Markdown.sublime-syntax",
    }
)
CHAT_SYNTAX_RESOURCE = syntax_resource("Codex.plugin.vendor.sublime_chat_ui")


def _syntax_path(view: Any) -> str | None:
    """Return the syntax resource persisted on *view*, if available."""

    path = view.settings().get("syntax")
    if isinstance(path, str) and path:
        return path

    try:
        syntax = view.syntax()
    except (AttributeError, RuntimeError):
        return None

    syntax_path = getattr(syntax, "path", None)
    return syntax_path if isinstance(syntax_path, str) else None


def migrate_chat_syntax(view: Any) -> bool:
    """Assign the shared syntax to a persisted Codex chat view when needed."""

    settings = view.settings()
    current = _syntax_path(view)
    target = CHAT_SYNTAX_RESOURCE

    if current == target:
        return False
    if current not in LEGACY_CHAT_SYNTAXES and not settings.get(TRANSCRIPT_VIEW_FLAG):
        return False

    view.assign_syntax(target)
    return True

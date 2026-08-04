"""Small Markdown-formatting helpers shared by host adapters."""

from __future__ import annotations

from typing import Optional


def fenced_code(text: str, language: str = "") -> str:
    """Wrap text in a fenced code block with stable trailing spacing."""

    body = text.rstrip("\n")
    return "```{}\n{}\n```\n\n".format(language, body)


def selection_markdown(
    text: str,
    file_path: Optional[str] = None,
    language: Optional[str] = None,
) -> str:
    """Format a selected source fragment for a prompt."""

    header = "**{}**\n\n".format(file_path) if file_path else ""
    return header + fenced_code(text, language or "")


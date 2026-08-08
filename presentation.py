"""Sublime view and panel presentation primitives.

This module deliberately defines no concrete sublime_plugin command classes.
Command names and backend callbacks belong to each host plugin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

try:
    from .markdown import section_break
except ImportError:  # Unit tests import this source module directly.
    from markdown import section_break

try:
    from sublime import Region as _Region  # type: ignore
except ImportError:  # Unit tests run outside Sublime's plugin host.
    _Region = None


def syntax_resource(package: Optional[str] = None) -> str:
    """Return the vendored Chat Markdown resource for the current host."""

    package_name = package if package is not None else __package__
    if not package_name:
        raise RuntimeError("syntax_resource() needs a package outside Sublime")
    return "Packages/{}/Syntaxes/ChatMarkdown.sublime-syntax".format(
        package_name.replace(".", "/")
    )


@dataclass(frozen=True)
class PanelPresentation:
    syntax: Optional[str] = None
    scroll_past_end: Optional[bool] = None
    gutter: Optional[bool] = None
    line_numbers: Optional[bool] = None
    fold_buttons: Optional[bool] = None
    word_wrap: Optional[bool] = None
    set_unsaved_view_name: Optional[bool] = None


INPUT_PRESENTATION = PanelPresentation(
    scroll_past_end=True,
    gutter=True,
    line_numbers=False,
    fold_buttons=False,
    word_wrap=True,
)

OUTPUT_PRESENTATION = PanelPresentation(
    scroll_past_end=True,
    gutter=True,
    line_numbers=False,
    fold_buttons=True,
    word_wrap=True,
    set_unsaved_view_name=False,
)


def apply_presentation(
    view: Any,
    presentation: PanelPresentation,
    markdown_syntax: Optional[str] = None,
) -> Any:
    """Apply the non-None settings from a presentation profile."""

    syntax = markdown_syntax if markdown_syntax is not None else presentation.syntax
    if syntax:
        view.assign_syntax(syntax)

    settings = view.settings()
    for key in (
        "scroll_past_end",
        "gutter",
        "line_numbers",
        "fold_buttons",
        "word_wrap",
        "set_unsaved_view_name",
    ):
        value = getattr(presentation, key)
        if value is not None:
            settings.set(key, value)
    return view


def replace_content(view: Any, text: str) -> None:
    """Replace all view content using commands valid for output panels."""

    view.run_command("select_all")
    view.run_command("right_delete")
    if text:
        view.run_command("append", {"characters": text, "force": True})


def view_text(view: Any) -> str:
    """Read all text from a Sublime view."""

    if _Region is None:
        raise RuntimeError("view_text() requires Sublime's Region API")
    return view.substr(_Region(0, view.size()))


def move_caret_to_end(view: Any) -> None:
    """Collapse selections to a single caret at the end of the view."""

    if _Region is None:
        raise RuntimeError("move_caret_to_end() requires Sublime's Region API")
    view.sel().clear()
    view.sel().add(_Region(view.size()))


def show_panel(window: Any, panel_name: str, panel: Optional[Any] = None) -> None:
    """Show an output panel and optionally focus its view."""

    window.run_command("show_panel", {"panel": "output.{}".format(panel_name)})
    if panel is not None:
        window.focus_view(panel)


def prepare_input_panel(
    window: Any,
    panel_name: str,
    initial_text: str = "",
    presentation: PanelPresentation = INPUT_PRESENTATION,
) -> Any:
    """Create, style, populate, show, and focus a writable input panel."""

    panel = window.create_output_panel(panel_name)
    panel.set_read_only(False)
    apply_presentation(panel, presentation, syntax_resource())
    replace_content(panel, initial_text)
    move_caret_to_end(panel)
    show_panel(window, panel_name, panel)
    panel.show(panel.size())
    return panel


def prepare_output_panel(
    window: Any,
    panel_name: str,
    presentation: PanelPresentation = OUTPUT_PRESENTATION,
) -> Any:
    """Find or create an output panel and apply its presentation."""

    panel = window.find_output_panel(panel_name) or window.create_output_panel(panel_name)
    apply_presentation(panel, presentation, syntax_resource())
    return panel


def append_text(view: Any, text: str) -> None:
    view.run_command("append", {"characters": text, "force": True})


def append_markdown_section(view: Any, header: str, body: str = "") -> int:
    """Append a transcript section and return its absolute header position."""

    start = view.size()
    prefix = section_break() if header else ""
    text = prefix + header + body
    header_start = start + len(prefix)

    if start > 0 and text and not text.startswith("\n") and view.substr(start - 1) != "\n":
        text = "\n" + text
        header_start += 1

    append_text(view, text)
    return header_start


def clear_view(view: Any, read_only: bool = True) -> None:
    view.set_read_only(False)
    replace_content(view, "")
    view.set_read_only(read_only)


def scroll_to_end(view: Any, center: bool = False) -> None:
    point = view.size()
    if center:
        view.show_at_center(point)
    else:
        view.show(point)

"""Source-only shared UI primitives for Sublime Text chat plugins."""

from .history import PromptHistorySession
from .markdown import fenced_code, selection_markdown
from .presentation import (
    INPUT_PRESENTATION,
    OUTPUT_PRESENTATION,
    PanelPresentation,
    append_text,
    apply_presentation,
    clear_view,
    move_caret_to_end,
    prepare_input_panel,
    prepare_output_panel,
    replace_content,
    scroll_to_end,
    show_panel,
    syntax_resource,
    view_text,
)

__all__ = [
    "INPUT_PRESENTATION",
    "OUTPUT_PRESENTATION",
    "PanelPresentation",
    "PromptHistorySession",
    "append_text",
    "apply_presentation",
    "clear_view",
    "fenced_code",
    "move_caret_to_end",
    "prepare_input_panel",
    "prepare_output_panel",
    "replace_content",
    "scroll_to_end",
    "selection_markdown",
    "show_panel",
    "syntax_resource",
    "view_text",
]


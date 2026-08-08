"""Source-only shared UI primitives for Sublime Text chat plugins."""

from .history import PromptHistorySession
from .links import MarkdownLink, local_file_target, markdown_link_at
from .markdown import fenced_code, section_break, selection_markdown
from .presentation import (
    INPUT_PRESENTATION,
    OUTPUT_PRESENTATION,
    PanelPresentation,
    append_markdown_section,
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
    "MarkdownLink",
    "OUTPUT_PRESENTATION",
    "PanelPresentation",
    "PromptHistorySession",
    "append_markdown_section",
    "append_text",
    "apply_presentation",
    "clear_view",
    "fenced_code",
    "local_file_target",
    "markdown_link_at",
    "move_caret_to_end",
    "prepare_input_panel",
    "prepare_output_panel",
    "replace_content",
    "scroll_to_end",
    "section_break",
    "selection_markdown",
    "show_panel",
    "syntax_resource",
    "view_text",
]

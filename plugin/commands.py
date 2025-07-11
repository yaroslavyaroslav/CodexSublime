"""Sublime Text commands and panel workflow for Codex."""

from __future__ import annotations

import uuid

import sublime  # type: ignore
import sublime_plugin  # type: ignore

from .bridge_manager import get_bridge

# ---------------------------------------------------------------------------
# Transcript view helpers
# ---------------------------------------------------------------------------


TRANSCRIPT_VIEW_FLAG = 'codex_is_transcript'


def _get_transcript_view(window: sublime.Window) -> sublime.View | None:  # type: ignore[name-defined]
    """Find and return the Codex transcript view in *window* (if any)."""

    for v in window.views():
        if v.settings().get(TRANSCRIPT_VIEW_FLAG):
            return v
    return None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(msg: dict) -> str | None:
    msg_type = msg.get('type')

    if msg_type == 'assistant_message':
        items = msg.get('items', [])
        return ''.join(i.get('text', '') for i in items if i.get('type') == 'text')

    for key in (
        'text',
        'message',
        'last_agent_message',
        'command',
        'stdout',
        'stderr',
    ):
        if key in msg:
            return str(msg.get(key, ''))

    return None


def _display_assistant_response(window: sublime.Window, prompt: str, event: dict) -> None:  # type: ignore[name-defined]
    """Append the Codex *event* to output panel using markdown formatting."""

    target_view = _get_transcript_view(window)

    if target_view is None:
        target_view = window.find_output_panel('codex') or window.create_output_panel('codex')
        is_panel = True
    else:
        is_panel = False

    target_view.set_read_only(False)
    target_view.assign_syntax('Packages/Markdown/MultiMarkdown.sublime-syntax')

    target_view.settings().set('scroll_past_end', True)
    target_view.settings().set('gutter', True)
    target_view.settings().set('line_numbers', False)

    msg = event.get('msg', {})
    msg_type: str = msg.get('type', 'unknown')

    text = _extract_text(msg)

    header = f'## {msg_type}\n\n'

    if text and msg_type == 'exec_command_end':
        # Sometime codex add \n to the end some times don't,
        # so it's better to be safe than sorry.
        body = f'```bash\n{text}\n```\n\n'
    else:
        body = (text + '\n\n') if text else ''

    target_view.run_command('append', {'characters': header + body, 'force': True})

    if not is_panel:
        # Scroll to bottom in tab view.
        target_view.show(target_view.size())

    # Restore read-only
    target_view.set_read_only(True)

    if is_panel:
        window.run_command('show_panel', {'panel': 'output.codex'})


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class CodexPromptCommand(sublime_plugin.TextCommand):
    """Open an *output panel* so the user can type a prompt."""

    INPUT_PANEL_NAME = 'codex_input'

    def run(self, edit: sublime.Edit) -> None:  # type: ignore[name-defined]
        window = self.view.window()
        if window is None:
            return

        panel = window.create_output_panel(self.INPUT_PANEL_NAME)
        panel.set_read_only(False)
        panel.assign_syntax('Packages/Markdown/MultiMarkdown.sublime-syntax')
        panel.settings().set('scroll_past_end', True)
        panel.settings().set('gutter', True)
        panel.settings().set('line_numbers', False)
        panel.settings().set('fold_buttons', False)

        # Pre-fill selection, if any, as a convenience.
        initial_text = self._selected_text()
        if initial_text:
            panel.run_command('append', {'characters': initial_text})

        window.run_command('show_panel', {'panel': f'output.{self.INPUT_PANEL_NAME}'})
        window.focus_view(panel)

    # ---------------------------------------------------------------------

    def _selected_text(self) -> str | None:
        for region in self.view.sel():
            if not region.empty():
                return self.view.substr(region)
        return None


class CodexSubmitInputPanelCommand(sublime_plugin.WindowCommand):
    """Submit the content of the *codex_input* panel to Codex (⌘/Ctrl+Enter)."""

    INPUT_PANEL_NAME = 'codex_input'

    def run(self) -> None:  # noqa: D401 – ST API shape
        panel_view = self.window.find_output_panel(self.INPUT_PANEL_NAME)
        if panel_view is None:
            sublime.status_message('Codex: no input panel open')
            return

        prompt = panel_view.substr(sublime.Region(0, panel_view.size())).strip()
        if not prompt:
            sublime.status_message('Codex: prompt is empty')
            return

        # Close the panel before sending to Codex.
        self.window.run_command('hide_panel')

        bridge = get_bridge(self.window)
        msg_id = str(uuid.uuid4())

        bridge.send(
            {
                'id': msg_id,
                'op': {
                    'type': 'user_input',
                    'items': [{'type': 'text', 'text': prompt}],
                },
            },
            cb=lambda event, p=prompt: _display_assistant_response(self.window, p, event),
        )

        # Show the user's prompt immediately.
        _display_assistant_response(
            self.window,
            prompt,
            {
                'msg': {
                    'type': 'user_input',
                    'text': prompt,
                }
            },
        )


# ---------------------------------------------------------------------------
# Transcript tab opener
# ---------------------------------------------------------------------------


class CodexOpenTranscriptCommand(sublime_plugin.WindowCommand):
    """Open (or focus) the dedicated Codex transcript tab."""

    def run(self) -> None:  # noqa: D401 – ST API shape
        view = _get_transcript_view(self.window)
        if view is None:
            view = self.window.new_file()
            view.set_name('Codex Transcript')
            view.set_scratch(True)
            view.assign_syntax('Packages/Markdown/MultiMarkdown.sublime-syntax')

            view.settings().set(TRANSCRIPT_VIEW_FLAG, True)

        self.window.focus_view(view)

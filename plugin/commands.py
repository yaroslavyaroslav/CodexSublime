"""Sublime Text commands and panel workflow for Codex."""

from __future__ import annotations

import uuid

import sublime  # type: ignore
import sublime_plugin  # type: ignore

from .bridge_manager import get_bridge

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

    panel = window.find_output_panel('codex') or window.create_output_panel('codex')
    panel.set_read_only(False)
    panel.assign_syntax('Packages/Markdown/MultiMarkdown.sublime-syntax')

    panel.settings().set('scroll_past_end', True)
    panel.settings().set('gutter', True)
    panel.settings().set('line_numbers', False)

    msg = event.get('msg', {})
    msg_type: str = msg.get('type', 'unknown')

    text = _extract_text(msg)

    header = f'## {msg_type}\n\n'
    body = (text + '\n\n') if text else ''

    panel.run_command('append', {'characters': header + body})

    panel.set_read_only(True)
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

        # Immediately show the user's prompt so it is visible before Codex
        # starts streaming any reasoning/result events.
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

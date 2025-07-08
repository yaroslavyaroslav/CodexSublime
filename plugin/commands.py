"""Sublime Text commands exposed by the Codex plugin."""

from __future__ import annotations

import uuid

import sublime
import sublime_plugin

from .bridge_manager import get_bridge


class CodexPromptCommand(sublime_plugin.TextCommand):
    """Send the selected text to Codex and display the answer in a panel."""

    def run(self, edit: sublime.Edit) -> None:  # type: ignore[name-defined]
        prompt = self._collect_prompt()
        if not prompt:
            sublime.status_message('Select some text first')
            return

        bridge = get_bridge(self.view.window())

        msg_id = str(uuid.uuid4())
        bridge.send(
            {
                'id': msg_id,
                'op': {
                    'type': 'user_input',
                    'items': [{'type': 'text', 'text': prompt}],
                },
            },
            cb=lambda event: self._handle_event(event, prompt),
        )

    # --------------------------------------------------------------------- helpers

    def _collect_prompt(self) -> str | None:
        view = self.view
        for region in view.sel():
            if not region.empty():
                return view.substr(region)
        return None

    def _handle_event(self, event: dict, prompt: str) -> None:  # noqa: D401 – simple
        msg = event.get('msg', {})
        msg_type = msg.get('type')

        # Collect any text we can display from the various message shapes.
        if msg_type == 'assistant_message':
            items = msg.get('items', [])
            text_items = [i.get('text', '') for i in items if i.get('type') == 'text']
        else:
            for key in (
                'text',
                'message',
                'last_agent_message',
                'command',
                'stdout',
                'stderr',
            ):
                if key in msg:
                    text_items = [msg.get(key, '')]
                    break
            else:
                return  # nothing we know how to display

        if not any(text_items):
            return

        window = self.view.window()
        assert window is not None

        panel = window.find_output_panel('codex') or window.create_output_panel('codex')
        panel.set_read_only(False)

        content = '>>> ' + prompt + '\n' + ''.join(text_items) + '\n\n'
        panel.run_command('append', {'characters': content})

        panel.set_read_only(True)
        window.run_command('show_panel', {'panel': 'output.codex'})

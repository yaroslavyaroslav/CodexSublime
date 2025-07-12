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

    body = ''
    if msg_type == 'task_started':
        header = '## Task started\n\n'
    elif msg_type == 'exec_command_begin':
        header = '### Command call\n\n'
        cmd_list = msg.get('command', [])
        cmd_str = ' '.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
        body = f'```bash\n{cmd_str}\n```\n\n'

    elif msg_type == 'exec_command_end':
        header = ''
        exit_code = msg.get('exit_code', 0)
        stderr = msg.get('stderr', '')
        stdout = msg.get('stdout', '')

        if exit_code and exit_code != 0:
            body += f'`exit_code: {exit_code}`\n\n'
            output_text = stderr if stderr else stdout
            label = 'stderr' if stderr else 'stdout'
        else:
            output_text = stdout if stdout else ''
            label = 'stdout' if output_text else ''

        if output_text:
            body += f'`{label}`:\n```\n{output_text}\n```\n\n'

    else:
        header = (
            f'## {msg_type}\n\n' if msg_type in ['user_input', 'agent_message'] else f'### {msg_type}\n\n'
        )
        text = _extract_text(msg)
        if text:
            body = f'{text}\n\n'

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

        # Pre-fill selection, if any, with optional code-fence wrapping.
        initial_text = self._collect_selection_with_fence()
        if initial_text:
            panel.run_command('append', {'characters': initial_text})

        # Put caret at end so user can continue typing.
        panel.sel().clear()
        panel.sel().add(sublime.Region(panel.size()))

        window.run_command('show_panel', {'panel': f'output.{self.INPUT_PANEL_NAME}'})
        window.focus_view(panel)

    # ---------------------------------------------------------------------

    def _collect_selection_with_fence(self) -> str | None:
        """Return selected text optionally wrapped in ``` fences for source.*."""

        for region in self.view.sel():
            if region.empty():
                continue

            text = self.view.substr(region)

            syntax = self.view.syntax()
            if syntax and syntax.scope.startswith('source.'):
                lang_token = syntax.name.split()[0].lower() if syntax.name else ''
                fenced = f'```{lang_token}\n{text}\n```\n\n'
                return fenced

            return text

        return None


class CodexSubmitInputPanelCommand(sublime_plugin.WindowCommand):
    """Submit the content of the *codex_input* panel to Codex (⌘/Ctrl+Enter)."""

    INPUT_PANEL_NAME = 'codex_input'

    def run(self) -> None:  # noqa: D401 – ST API shape
        panel_view = self.window.find_output_panel(self.INPUT_PANEL_NAME)
        if panel_view is None:
            sublime.status_message('no input panel open')
            return

        prompt = panel_view.substr(sublime.Region(0, panel_view.size())).strip()
        if not prompt:
            sublime.status_message('prompt is empty')
            return

        # Close and destroy the input panel so it does not linger in the panel list.
        self.window.run_command('hide_panel')
        self.window.destroy_output_panel(self.INPUT_PANEL_NAME)

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
        newly_created = False
        if view is None:
            view = self.window.new_file()
            newly_created = True
            view.set_name('Codex')
            view.set_scratch(True)
            view.assign_syntax('Packages/Markdown/MultiMarkdown.sublime-syntax')
            view.settings().set(TRANSCRIPT_VIEW_FLAG, True)

        # If we just created the tab, seed it with the existing output panel
        # contents (if any) so earlier conversation context is preserved.
        if newly_created:
            panel_view = self.window.find_output_panel('codex')
            if panel_view is not None:
                content = panel_view.substr(sublime.Region(0, panel_view.size()))
                if content:
                    view.run_command('append', {'characters': content, 'force': True})

        self.window.focus_view(view)


# ---------------------------------------------------------------------------
# Reset chat command
# ---------------------------------------------------------------------------


class CodexResetChatCommand(sublime_plugin.WindowCommand):
    """Clear transcript / panel and terminate the Codex subprocess for window."""

    def run(self) -> None:  # noqa: D401 – ST API shape
        from . import bridge_manager as bm

        # 1. Terminate existing bridge (if any)
        key = self.window.id()
        bridge = bm.bridges.pop(key, None)
        if bridge is not None:
            bridge.terminate()

        # 2. Clear transcript view
        transcript = _get_transcript_view(self.window)
        if transcript is not None:
            transcript.set_read_only(False)
            transcript.run_command('select_all')
            transcript.run_command('right_delete')
            transcript.set_read_only(True)

        # 3. Clear output panel
        panel_view = self.window.find_output_panel('codex')
        if panel_view is not None:
            panel_view.set_read_only(False)
            panel_view.run_command('select_all')
            panel_view.run_command('right_delete')
            panel_view.set_read_only(True)

        # 4. Remove persisted session_id (if any) so that the next prompt
        #    starts a brand new conversation but keeps other Codex project
        #    configuration intact.
        data = self.window.project_data()
        if data is not None:
            settings_block = data.get('settings') or {}
            codex_cfg = settings_block.get('codex') or {}

            if 'session_id' in codex_cfg:
                codex_cfg['session_id'] = None
                settings_block['codex'] = codex_cfg
                data['settings'] = settings_block
                self.window.set_project_data(data)

        sublime.status_message('Codex chat reset – new session will start with next prompt')

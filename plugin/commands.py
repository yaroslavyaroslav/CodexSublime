"""Sublime Text commands and panel workflow for Codex."""

from __future__ import annotations

import json
import logging
import uuid
import os

import sublime  # type: ignore
import sublime_plugin  # type: ignore

from .bridge_manager import get_bridge

logger = logging.getLogger(__name__)
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


def _display_assistant_response(window: sublime.Window, prompt: str, event: dict, session_id: str) -> None:  # type: ignore[name-defined]
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

    # ------------------------------------------------------------------
    # "Run process?" approval request -------------------------------------------------
    # ------------------------------------------------------------------

    elif msg_type == 'exec_approval_request':
        # Display the command that requests approval and immediately open a
        # quick-panel so the user can choose how to proceed.  We *must* reply
        # to the Codex backend, otherwise it will keep waiting for ever.

        header = '### exec_approval\n\n'

        cmd_list = msg.get('command', [])
        cmd_str = ' '.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
        body = f'```bash\n{cmd_str}\n```\n\n'

        # Offer the same five options the Codex CLI exposes in interactive
        # mode so the Sublime UX mirrors the terminal behaviour.
        quick_panel_items = [
            ['Yes', 'Run this command once'],
            ['Always Yes', 'Always allow this exact command without asking'],
            ['No', 'Reject the command'],
            ['Abort Execution', 'Stop session completely'],
        ]

        def _on_done(index: int, *, _window=window, _event=event):  # noqa: D401 – callback
            # Map the selected quick-panel index to the corresponding choice
            # understood by the CLI.  If the user aborted the panel (index
            # == -1) we treat it as an explicit *no*.

            choice_map = {
                0: 'approved',
                1: 'approved_for_session',
                2: 'denied',
                3: 'abort',
                -1: 'denied',
            }

            decision = choice_map.get(index, 'denied')
            logger.debug('call id for approval: %s', _event.get('id', 'wrong_id'))
            # Send the approval response back to the Codex bridge so the
            # conversation can continue.
            bridge = get_bridge(_window)
            bridge.send(
                {
                    'id': session_id,  # keep the same conversation id
                    'op': {
                        'id': _event.get('id'),  # keep the same conversation id
                        'type': 'exec_approval',
                        'decision': decision,
                    },
                },
            )

        # Show the quick-panel *after* we appended the request to the
        # transcript so both happen in a single UI update.
        window.show_quick_panel(quick_panel_items, _on_done)

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

    # ------------------------------------------------------------------
    # MCP tool-call wrappers -------------------------------------------
    # ------------------------------------------------------------------

    elif msg_type == 'mcp_tool_call_begin':
        # Display information about the tool invocation in a concise form.
        header = '### Tool call\n\n'

        server = msg.get('server', '')
        tool = msg.get('tool', '')
        call_id = msg.get('call_id', '')
        arguments = msg.get('arguments', {})

        args_json = json.dumps(arguments, indent=2) if arguments else '{}'

        body = (
            f'`server`: `{server}`  \n'
            f'`tool`: `{tool}`  \n'
            f'`call_id`: `{call_id}`\n\n'
            f'```json\n{args_json}\n```\n\n'
        )

    elif msg_type == 'mcp_tool_call_end':
        header = ''  # keep output compact – this follows command_end style.

        result = msg.get('result', {})

        if 'Ok' in result:
            ok_payload = result['Ok']

            # DuckDuckGo and other servers often return a list of content
            # items.  Extract plain-text segments so the transcript remains
            # readable without overwhelming markdown formatting.
            content_items = ok_payload.get('content', []) if isinstance(ok_payload, dict) else []

            if content_items and isinstance(content_items, list):
                texts = [c.get('text', '') for c in content_items if isinstance(c, dict)]
                result_text = '\n'.join(texts).strip()
            else:
                # Fallback: pretty-print JSON for any other payload.
                result_text = json.dumps(ok_payload, indent=2)

            body = f'```\n{result_text}\n```\n\n'

        elif 'Err' in result:
            err_payload = result['Err']
            body = f'`Error`: {err_payload}\n\n'
        else:
            # Unexpected shape – show raw result.
            body = f'```json\n{json.dumps(result, indent=2)}\n```\n\n'

    # ------------------------------------------------------------------
    # apply_patch wrapper events ---------------------------------------
    # ------------------------------------------------------------------

    elif msg_type == 'patch_apply_begin':
        header = '### Applying patch\n\n'

        auto_approved = msg.get('auto_approved', False)
        body = f'`auto_approved`: {auto_approved}\n\n'

        changes = msg.get('changes', {})
        if isinstance(changes, dict):
            for file_path, change_info in changes.items():
                # Determine operation type.
                op_type = next(iter(change_info.keys()), '') if isinstance(change_info, dict) else ''
                body += f'**{file_path}** ({op_type})\n'

                diff_payload = change_info.get(op_type, {}) if isinstance(change_info, dict) else {}
                unified_diff = diff_payload.get('unified_diff') if isinstance(diff_payload, dict) else None

                if unified_diff:
                    # Wrap diff in a code block so it renders nicely in Markdown.
                    body += f'```diff\n{unified_diff}\n```\n\n'

    elif msg_type == 'patch_apply_end':
        header = ''

        success = msg.get('success', False)
        stdout = msg.get('stdout', '')
        stderr = msg.get('stderr', '')

        body = f'`success`: {success}\n\n'

        if stdout:
            body += f'```\n{stdout}\n```\n\n'

        if stderr:
            body += f'`stderr`:\n```\n{stderr}\n```\n\n'

    else:
        header = (
            f'## {msg_type}\n\n' if msg_type in ['user_input', 'agent_message'] else f'### {msg_type}\n\n'
        )
        text = _extract_text(msg)
        if text:
            body = f'{text}\n\n'

    # Determine whether the caret was at end before appending so we can
    # preserve the reader's position unless they were following the tail.
    will_follow_tail = False
    if not is_panel:
        try:
            pre_size = target_view.size()
            selections = list(target_view.sel())
            will_follow_tail = any(r.empty() and r.end() == pre_size for r in selections)
        except Exception:
            # If anything goes wrong, default to current behaviour (follow tail).
            will_follow_tail = True

    target_view.run_command('append', {'characters': header + body, 'force': True})

    if not is_panel and will_follow_tail:
        # Only auto-scroll in the transcript tab when the caret was at the
        # very end before we appended new content.
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

        # Ensure the panel is scrolled to the very end so the caret is visible
        # even if we pre-filled a long selection.
        panel.show(panel.size())

    # ---------------------------------------------------------------------

    def _collect_selection_with_fence(self) -> str | None:
        """Return selected text (first region) prefixed with the file path and
        optionally wrapped in Markdown code fences when the selection comes
        from a *source.* syntax view.
        """

        for region in self.view.sel():
            if region.empty():
                continue

            selected = self.view.substr(region)

            # Determine a useful path representation (relative to the first
            # project folder if possible, otherwise absolute).
            path_header = ''
            file_path = self.view.file_name()
            if file_path:
                window = self.view.window()
                if window:
                    folders = window.folders()
                    if folders:
                        try:
                            rel = os.path.relpath(file_path, folders[0])
                            file_path = rel  # less noisy than absolute
                        except ValueError:
                            pass  # keep absolute path if relpath fails

                path_header = f'**{file_path}**\n\n'

            syntax = self.view.syntax()
            if syntax and syntax.scope.startswith('source.'):
                lang_token = syntax.name.split()[0].lower() if syntax.name else ''
                body = f'```{lang_token}\n{selected}\n```\n\n'
            else:
                body = f'```\n{selected}\n```\n\n'

            return path_header + body

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
        project_data = self.window.project_data()
        settings_block = project_data.get('settings') or {}
        codex_cfg = settings_block.get('codex') or {}
        session_id: str = None  # type: ignore
        if 'session_id' in codex_cfg:
            session_id = codex_cfg['session_id']
        msg_id = str(uuid.uuid4())

        bridge.send(
            {
                'id': msg_id,
                'op': {
                    'type': 'user_input',
                    'items': [{'type': 'text', 'text': prompt}],
                },
            },
            cb=lambda event, p=prompt: _display_assistant_response(self.window, p, event, session_id),
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
            session_id,
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

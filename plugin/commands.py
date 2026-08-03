"""Sublime Text commands and panel workflow for Codex."""

import json
import logging
import os
import uuid
from datetime import UTC, datetime

import sublime  # type: ignore
import sublime_plugin  # type: ignore

from .bridge_manager import get_bridge

logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Transcript view helpers
# ---------------------------------------------------------------------------


TRANSCRIPT_VIEW_FLAG = 'codex_is_transcript'
HIDDEN_TRANSCRIPT_TYPES = {
    # Infra/bootstrap noise from app-server runtime.
    'mcp_startup_update',
    'mcp_startup_complete',
    # Item lifecycle bookkeeping; usually duplicates more useful events.
    'item_started',
    'item_completed',
    # Streaming deltas are noisy in transcript unless explicitly rendered.
    'agent_message_delta',
    'reasoning_content_delta',
    # The plugin already echoes the user prompt right after submit.
    'user_message',
}
STREAMING_AGENT_BLOCKS: dict[tuple[int, str], dict[str, str]] = {}


def _is_debug_logging_enabled() -> bool:
    try:
        level = sublime.load_settings('Codex.sublime-settings').get('log_level', '')
        return isinstance(level, str) and level.lower() == 'debug'
    except Exception:
        return False


def _cmd_trace(message: str, *args: object) -> None:
    if not _is_debug_logging_enabled():
        return
    try:
        text = message % args if args else message
    except Exception:
        text = f'{message} {args!r}'
    line = f'[{datetime.now(UTC).isoformat()}] {text}\n'
    for path in ('/tmp/codex_sublime_commands.log', '/tmp/codex_sublime_main.log'):
        try:
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line)
        except Exception:
            pass


_cmd_trace('commands module imported')


def _package_name() -> str:
    """Return the Sublime package folder name for this plugin."""
    try:
        # commands.py lives in <package>/plugin/commands.py
        package = os.path.basename(os.path.dirname(os.path.dirname(__file__)))

        # Sublime exposes resources via ``Packages/<name>`` even when the
        # package is installed as ``<name>.sublime-package`` inside
        # ``Installed Packages``.  When running from the packed archive, the
        # filesystem path includes the ``.sublime-package`` suffix and we must
        # strip it; otherwise Sublime looks for
        # ``Packages/<name>.sublime-package`` which does not exist.
        if package.endswith('.sublime-package'):
            package = os.path.splitext(package)[0]

        return package
    except Exception:
        # Fallback to the expected package name when installed per README
        return 'Codex'


def _markdown_syntax_resource() -> str:
    """Return resource path to the bundled Markdown syntax."""
    return f"Packages/{_package_name()}/Syntaxes/Markdown.sublime-syntax"


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


def _get_fold_section_names(window: sublime.Window) -> set[str]:  # type: ignore[name-defined]
    """Return a lowercased set of section names to auto-fold.

    Reads from per-project settings at settings.codex.fold_sections, falling
    back to global Codex.sublime-settings → fold_sections. Accepts a string
    or list of strings. Comparison is case-insensitive.
    """

    names: set[str] = set()

    try:
        project_data = window.project_data() or {}
        settings_block = project_data.get('settings') or {}
        codex_cfg = settings_block.get('codex') or {}
        proj = codex_cfg.get('fold_sections')
        if isinstance(proj, str):
            names.add(proj.strip().lower())
        elif isinstance(proj, list):
            for n in proj:
                if isinstance(n, str):
                    names.add(n.strip().lower())
    except Exception:
        pass

    try:
        global_settings = sublime.load_settings('Codex.sublime-settings')
        glob = global_settings.get('fold_sections')
        if isinstance(glob, str):
            names.add(glob.strip().lower())
        elif isinstance(glob, list):
            for n in glob:
                if isinstance(n, str):
                    names.add(n.strip().lower())
    except Exception:
        pass

    return names


def _format_patch_changes(changes: dict) -> str:
    """Return a Markdown representation of patch *changes*."""

    body = ''

    if not isinstance(changes, dict):
        try:
            dump = json.dumps(changes, indent=2)
            return f'```json\n{dump}\n```\n\n'
        except Exception:
            dump = str(changes)
            return f'```\n{dump}\n```\n\n'

    for file_path, change_info in changes.items():
        if not isinstance(change_info, dict):
            continue

        file_label = str(file_path)
        op_type = next(iter(change_info.keys()), '')
        op_suffix = f' ({op_type})' if op_type else ''
        body += f'**{file_label}**{op_suffix}\n'

        diff_payload = change_info.get(op_type, {}) if isinstance(change_info, dict) else {}
        if isinstance(diff_payload, dict):
            unified_diff = diff_payload.get('unified_diff')
            if unified_diff:
                body += f'```diff\n{unified_diff}\n```\n\n'

    return body


def _display_assistant_response(window: sublime.Window, prompt: str, event: dict, session_id: str) -> None:  # type: ignore[name-defined]
    """Append the Codex *event* to output panel using markdown formatting."""

    target_view = _get_transcript_view(window)

    if target_view is None:
        target_view = window.find_output_panel('codex') or window.create_output_panel('codex')
        is_panel = True
    else:
        is_panel = False

    target_view.set_read_only(False)
    target_view.assign_syntax(_markdown_syntax_resource())

    target_view.settings().set('scroll_past_end', True)
    target_view.settings().set('gutter', True)
    target_view.settings().set('line_numbers', False)

    msg = event.get('msg', {})
    msg_type: str = msg.get('type', 'unknown')

    stream_key = _agent_stream_key(window, event, msg)
    if msg_type == 'agent_message_content_delta':
        _append_agent_message_delta(window, target_view, is_panel, stream_key, msg)
        return

    if msg_type == 'agent_message' and stream_key is not None:
        streaming_state = STREAMING_AGENT_BLOCKS.get(stream_key)
        if streaming_state is not None:
            streamed = streaming_state.get('text', '')
            final_text = _extract_text(msg) or ''
            if not final_text or final_text.startswith(streamed):
                suffix = final_text[len(streamed):] if final_text else ''
                if suffix:
                    target_view.run_command('append', {'characters': suffix, 'force': True})
                target_view.run_command('append', {'characters': '\n\n', 'force': True})
                STREAMING_AGENT_BLOCKS.pop(stream_key, None)
                target_view.set_read_only(True)
                if is_panel:
                    window.run_command('show_panel', {'panel': 'output.codex'})
                return
            STREAMING_AGENT_BLOCKS.pop(stream_key, None)

    if msg_type in HIDDEN_TRANSCRIPT_TYPES:
        _cmd_trace('suppress transcript msg_type=%s', msg_type)
        return

    body = ''
    header_title_for_fold: str | None = None  # extracted header label for auto-folding
    if msg_type == 'task_started':
        header = '## Task started\n\n'
        header_title_for_fold = 'Task started'
    elif msg_type == 'exec_command_begin':
        header = '### Command Call\n\n'
        header_title_for_fold = 'Command Call'
        cmd_list = msg.get('command', [])
        cmd_str = ' '.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
        cwd_line = ''
        try:
            cwd_val = msg.get('cwd')
            if cwd_val:
                cwd_line = f'`cwd`: `{cwd_val}`\n\n'
        except Exception:
            pass
        body = cwd_line + f'```bash\n{cmd_str}\n```\n\n'

    # ------------------------------------------------------------------
    # "Run process?" approval request -------------------------------------------------
    # ------------------------------------------------------------------

    elif msg_type == 'exec_approval_request':
        # Display the command that requests approval and immediately open a
        # quick-panel so the user can choose how to proceed.  We *must* reply
        # to the Codex backend, otherwise it will keep waiting for ever.

        header = '### exec_approval\n\n'
        header_title_for_fold = 'exec_approval'

        cmd_list = msg.get('command', [])
        cmd_str = ' '.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
        body = f'```bash\n{cmd_str}\n```\n\n'

        # Offer the same options the Codex CLI exposes in interactive
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
            # Mirror the user's decision in transcript so rejected/cancelled
            # approvals are explicitly visible.
            _display_assistant_response(
                _window,
                '',
                {
                    'msg': {
                        'type': 'exec_approval_result',
                        'decision': decision,
                    }
                },
                session_id,
            )

        # Show the quick-panel *after* we appended the request to the
        # transcript so both happen in a single UI update.
        window.show_quick_panel(quick_panel_items, _on_done)

    elif msg_type == 'exec_command_end':
        header = '### Command Output\n\n'
        header_title_for_fold = 'Command Output'
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
        header_title_for_fold = 'Tool call'

        # Prefer the canonical Codex schema: `invocation.{server, tool, arguments}`
        invocation = msg.get('invocation') if isinstance(msg.get('invocation'), dict) else None

        # Be liberal in what we accept – if `invocation` is absent, fall back
        # to alternative shapes some servers may use.
        raw_server = (invocation.get('server') if invocation else None) or msg.get('server')
        if isinstance(raw_server, dict):
            server = raw_server.get('name') or raw_server.get('id') or raw_server.get('identifier') or ''
        else:
            server = str(raw_server) if raw_server is not None else ''

        raw_tool = (invocation.get('tool') if invocation else None) or msg.get('tool')
        if isinstance(raw_tool, dict):
            tool = raw_tool.get('name') or raw_tool.get('id') or raw_tool.get('identifier') or ''
        else:
            tool = str(raw_tool) if raw_tool is not None else ''

        # Additional fallbacks some servers may use.
        if not server:
            server = (
                msg.get('server_name')
                or msg.get('server_id')
                or msg.get('server_identifier')
                or ''
            )
        if not tool:
            tool = (
                msg.get('tool_name')
                or msg.get('tool_id')
                or msg.get('tool_identifier')
                or ''
            )

        # Best-effort extraction of a method/function/procedure name used by
        # the tool, and its arguments.  Different servers use different keys.
        method = (
            msg.get('method')
            or msg.get('method_name')
            or msg.get('procedure')
            or msg.get('function')
            or (invocation.get('method') if invocation else None)
            or (raw_tool.get('method') if isinstance(raw_tool, dict) else None)
            or ''
        )

        call_id = msg.get('call_id', '')

        # Gather arguments from canonical `invocation.arguments`, then common fallbacks.
        arguments = (
            (invocation.get('arguments') if invocation else None)
            or msg.get('arguments')
            or msg.get('args')
            or msg.get('params')
            or msg.get('parameters')
            or msg.get('input')
            or (raw_tool.get('arguments') if isinstance(raw_tool, dict) else None)
            or {}
        )

        # Header lines
        body = (
            f'`server`: `{server}`  \n' if server else ''
        )
        body += f'`tool`: `{tool}`  \n' if tool else ''
        # Only show `method` when it is explicitly provided and differs from `tool`.
        try:
            tool_cmp = (tool or '').strip().lower()
            method_cmp = (method or '').strip().lower()
        except Exception:
            tool_cmp = str(tool)
            method_cmp = str(method)

        if method and method_cmp != tool_cmp:
            body += f'`method`: `{method}`  \n'
        body += f'`call_id`: `{call_id}`\n\n' if call_id else '\n'

        # Always render an arguments block, even when empty, as requested.
        try:
            args_block = json.dumps(arguments, indent=2)
        except Exception:
            # Arguments might be a raw string; try to parse JSON-looking text.
            try:
                if isinstance(arguments, str):
                    stripped = arguments.strip()
                    if stripped.startswith('{') or stripped.startswith('['):
                        args_block = json.dumps(json.loads(stripped), indent=2)
                    else:
                        args_block = json.dumps({'value': arguments}, indent=2)
                else:
                    args_block = json.dumps({'value': str(arguments)}, indent=2)
            except Exception:
                args_block = json.dumps({'value': str(arguments)}, indent=2)

        body += f'```json\n{args_block}\n```\n\n'

    elif msg_type == 'mcp_tool_call_end':
        header = ''  # keep output compact – this follows command_end style.

        result = msg.get('result', {})

        def _looks_like_json(s: str) -> bool:
            s = s.strip()
            return (s.startswith('{') and s.endswith('}')) or (s.startswith('[') and s.endswith(']'))

        if 'Ok' in result:
            ok_payload = result['Ok']

            display_json = None
            display_text = None

            if isinstance(ok_payload, dict):
                # Prefer structured content if provided.
                sc = ok_payload.get('structuredContent') or ok_payload.get('structured_content')
                if isinstance(sc, dict) and 'result' in sc:
                    display_json = sc['result']
                else:
                    content_items = ok_payload.get('content', [])
                    if isinstance(content_items, list) and content_items:
                        texts = [c.get('text', '') for c in content_items if isinstance(c, dict)]
                        texts = [t for t in texts if t]
                        if texts and all(_looks_like_json(t) for t in texts):
                            # Aggregate JSON lines into an array for nicer formatting.
                            try:
                                parsed = [json.loads(t) for t in texts]
                                display_json = parsed
                            except Exception:
                                display_text = '\n'.join(texts).strip()
                        else:
                            display_text = '\n'.join(texts).strip()
                    else:
                        # Fallback: pretty-print entire payload.
                        display_json = ok_payload
            else:
                # Non-dict payload – show as text.
                display_text = str(ok_payload)

            if display_json is not None:
                body = f'```json\n{json.dumps(display_json, indent=2)}\n```\n\n'
            elif display_text is not None:
                body = f'```\n{display_text}\n```\n\n'
            else:
                body = '``````\n\n'  # unreachable but safe default

        elif 'Err' in result:
            err_payload = result['Err']
            # Render errors in a readable, consistent way.
            try:
                body = f'`Error`: {json.dumps(err_payload)}\n\n'
            except Exception:
                body = f'`Error`: {err_payload}\n\n'
        else:
            # Unexpected shape – show raw result.
            body = f'```json\n{json.dumps(result, indent=2)}\n```\n\n'

    # ------------------------------------------------------------------
    # apply_patch wrapper events ---------------------------------------
    # ------------------------------------------------------------------

    elif msg_type == 'apply_patch_approval_request':
        header = '### Apply changes?\n\n'
        header_title_for_fold = 'apply_patch_approval'

        body += _format_patch_changes(msg.get('changes', {}))

        quick_panel_items = [
            ['Yes', 'Apply these changes'],
            ['Always Yes', 'Always allow this exact patch without asking'],
            ['No', 'Reject the patch but keep the session running'],
            ['Abort Execution', 'Stop session completely'],
        ]

        def _on_done(index: int, *, _window=window, _event=event):  # noqa: D401 – callback
            choice_map = {
                0: 'approved',
                1: 'approved_for_session',
                2: 'denied',
                3: 'abort',
                -1: 'denied',
            }

            decision = choice_map.get(index, 'denied')
            logger.debug('call id for approval: %s', _event.get('id', 'wrong_id'))
            bridge = get_bridge(_window)
            bridge.send(
                {
                    'id': session_id,
                    'op': {
                        'id': _event.get('id'),
                        'type': 'patch_approval',
                        'decision': decision,
                    },
                },
            )
            _display_assistant_response(
                _window,
                '',
                {
                    'msg': {
                        'type': 'apply_patch_approval_result',
                        'decision': decision,
                    }
                },
                session_id,
            )

        window.show_quick_panel(quick_panel_items, _on_done)

    elif msg_type == 'patch_apply_begin':
        header = '### Applying patch\n\n'
        header_title_for_fold = 'Applying patch'

        auto_approved = msg.get('auto_approved', False)
        body = f'`auto_approved`: {auto_approved}\n\n'

        body += _format_patch_changes(msg.get('changes', {}))

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

    elif msg_type == 'exec_approval_result':
        header = '### exec_approval_result\n\n'
        header_title_for_fold = 'exec_approval_result'
        decision = str(msg.get('decision', 'unknown'))
        body = f'`decision`: `{decision}`\n\n'

    elif msg_type == 'apply_patch_approval_result':
        header = '### apply_patch_approval_result\n\n'
        header_title_for_fold = 'apply_patch_approval_result'
        decision = str(msg.get('decision', 'unknown'))
        body = f'`decision`: `{decision}`\n\n'

    elif msg_type == 'error':
        header = '### Error\n\n'
        header_title_for_fold = 'error'
        reason = msg.get('reason')
        message = msg.get('message') or msg.get('text')
        protocol = msg.get('protocol')
        stderr = msg.get('stderr')

        if reason:
            body += f'`reason`: `{reason}`\n\n'
        if protocol:
            body += f'`protocol`: `{protocol}`\n\n'
        if message:
            body += f'{message}\n\n'
        if stderr:
            body += f'`stderr`:\n```\n{stderr}\n```\n\n'
        if not body:
            body = '_No error details were provided._\n\n'

    else:
        header = (
            f'## {msg_type}\n\n' if msg_type in ['user_input', 'agent_message'] else f'### {msg_type}\n\n'
        )
        try:
            # Remove leading hashes and whitespace to get a readable title.
            header_line = header.splitlines()[0]
            header_title_for_fold = header_line.lstrip('#').strip()
        except Exception:
            header_title_for_fold = None
        text = _extract_text(msg)
        if text:
            body = f'{text}\n\n'
        else:
            payload = {k: v for k, v in msg.items() if k != 'type'}
            if payload:
                body = '_Unhandled message payload shown for inspection._\n\n'
                try:
                    body += f'```json\n{json.dumps(payload, indent=2)}\n```\n\n'
                except Exception:
                    body += f'```\n{repr(payload)}\n```\n\n'
            else:
                body = '_No renderable content available for this message._\n\n'

    # Determine whether the caret was at end before appending so we can
    # preserve the reader's position unless they were following the tail.
    will_follow_tail = False
    pre_size = 0
    if not is_panel:
        try:
            pre_size = target_view.size()
            selections = list(target_view.sel())
            will_follow_tail = any(r.empty() and r.end() == pre_size for r in selections)
        except Exception:
            # If anything goes wrong, default to current behaviour (follow tail).
            will_follow_tail = True
    else:
        # Still capture pre_size for panels so we know where the header begins.
        try:
            pre_size = target_view.size()
        except Exception:
            pre_size = 0

    # Ensure section headers start on a new line. If the buffer doesn't end
    # with a newline and we're about to append content that doesn't start
    # with one, prefix a single "\n" so folded previews don't run headers
    # together like "## A … ## B" on the same line.
    to_append = header + body
    try:
        needs_leading_nl = False
        if pre_size > 0 and to_append and not to_append.startswith('\n'):
            last_char = target_view.substr(sublime.Region(pre_size - 1, pre_size))
            needs_leading_nl = last_char != '\n'
        if needs_leading_nl:
            to_append = '\n' + to_append
    except Exception:
        pass

    target_view.run_command('append', {'characters': to_append, 'force': True})

    # Auto-fold freshly appended section when configured to do so.
    try:
        fold_names = _get_fold_section_names(window)
        should_fold = bool(header_title_for_fold and fold_names and header_title_for_fold.strip().lower() in fold_names)
        if should_fold and pre_size >= 0:
            # Defer folding slightly to allow syntax scopes to update, so the
            # new meta.section exists and we don't accidentally fold the previous one.
            def _attempt_fold(tries_left: int = 6) -> None:
                try:
                    if tries_left <= 0 or target_view.is_loading():
                        return
                    # Determine the start of the newly added header.
                    probe = pre_size
                    try:
                        if to_append.startswith('\n'):
                            probe = pre_size + 1
                    except Exception:
                        pass

                    sections = target_view.find_by_selector('meta.section')
                    # Work with sections sorted by start
                    sections.sort(key=lambda r: r.begin())
                    if not sections:
                        sublime.set_timeout(lambda: _attempt_fold(tries_left - 1), 50)
                        return

                    # Prefer the section whose begin matches the header line begin.
                    header_line_begin = target_view.line(probe).begin()
                    exact = [r for r in sections if r.begin() == header_line_begin]
                    def _fold_row_style(sec: sublime.Region) -> bool:
                        try:
                            # Fold only the section body (exclude header line) and
                            # leave the newline before the next header visible to
                            # prevent inline joining like "... ## next".
                            header_line = target_view.line(probe)
                            body_start = header_line.end()

                            # Find the next section's start after this one
                            next_begin = None
                            for r in sections:
                                if r.begin() > sec.begin():
                                    next_begin = r.begin()
                                    break

                            if next_begin is None:
                                # Last section: fold to section end, but prefer to
                                # leave a trailing newline (if present) out of the fold
                                end = sec.end()
                                try:
                                    if end - 1 >= 0 and target_view.substr(sublime.Region(end - 1, end)) == '\n':
                                        end = end - 1
                                except Exception:
                                    pass
                                body_end = end
                            else:
                                # Fold up to just before the next header's first char
                                # (i.e., exclude the newline preceding it).
                                body_end = max(body_start, next_begin - 1)

                            if body_start < body_end:
                                target_view.fold(sublime.Region(body_start, body_end))
                                return True
                        except Exception:
                            pass
                        return False

                    if exact:
                        if _fold_row_style(exact[0]):
                            return

                    # Fallback: fold the last section (highest begin) – most likely the new one.
                    last = sections[-1]
                    if last.contains(probe) or last.begin() >= header_line_begin:
                        if _fold_row_style(last):
                            return

                    # If scopes are still stale, retry shortly.
                    sublime.set_timeout(lambda: _attempt_fold(tries_left - 1), 50)
                except Exception:
                    pass

            sublime.set_timeout(_attempt_fold, 30)
    except Exception:
        # Never let folding errors break output updates.
        pass

    if not is_panel and will_follow_tail:
        # Only auto-scroll in the transcript tab when the caret was at the
        # very end before we appended new content.
        target_view.show(target_view.size())

    # Restore read-only
    target_view.set_read_only(True)

    if is_panel:
        window.run_command('show_panel', {'panel': 'output.codex'})


def _agent_stream_key(window: sublime.Window, event: dict, msg: dict) -> tuple[int, str] | None:  # type: ignore[name-defined]
    raw = (
        event.get('id')
        or msg.get('turn_id')
        or msg.get('turnId')
        or msg.get('item_id')
        or msg.get('itemId')
    )
    if raw is None:
        return None
    try:
        return (window.id(), str(raw))
    except Exception:
        return None


def _append_agent_message_delta(
    window: sublime.Window,  # type: ignore[name-defined]
    target_view: sublime.View,  # type: ignore[name-defined]
    is_panel: bool,
    stream_key: tuple[int, str] | None,
    msg: dict,
) -> None:
    delta = msg.get('delta')
    if not isinstance(delta, str) or not delta:
        target_view.set_read_only(True)
        return

    if stream_key is None:
        target_view.run_command('append', {'characters': delta, 'force': True})
        target_view.set_read_only(True)
        if is_panel:
            window.run_command('show_panel', {'panel': 'output.codex'})
        return

    state = STREAMING_AGENT_BLOCKS.get(stream_key)
    if state is None:
        state = {'text': '', 'started': ''}
        STREAMING_AGENT_BLOCKS[stream_key] = state

    if not state.get('started'):
        pre_size = target_view.size()
        to_append = '## agent_message\n\n'
        try:
            if pre_size > 0:
                last_char = target_view.substr(sublime.Region(pre_size - 1, pre_size))
                if last_char != '\n':
                    to_append = '\n' + to_append
        except Exception:
            pass
        target_view.run_command('append', {'characters': to_append, 'force': True})
        state['started'] = '1'

    state['text'] = state.get('text', '') + delta
    target_view.run_command('append', {'characters': delta, 'force': True})
    target_view.show(target_view.size())
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
        _cmd_trace('prompt command entered')
        window = self.view.window()
        if window is None:
            _cmd_trace('prompt aborted: no window')
            return

        panel = window.create_output_panel(self.INPUT_PANEL_NAME)
        panel.set_read_only(False)
        panel.assign_syntax(_markdown_syntax_resource())
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
        _cmd_trace('input panel shown and focused')

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
        _cmd_trace('submit command entered')
        session_id: str = ''  # used in error rendering before project settings load
        panel_view = self.window.find_output_panel(self.INPUT_PANEL_NAME)
        if panel_view is None:
            _cmd_trace('submit aborted: panel missing')
            sublime.status_message('no input panel open')
            return

        prompt = panel_view.substr(sublime.Region(0, panel_view.size())).strip()
        if not prompt:
            _cmd_trace('submit aborted: empty prompt')
            sublime.status_message('prompt is empty')
            return

        _cmd_trace('submit prompt chars=%d', len(prompt))
        self.window.run_command('hide_panel')

        try:
            _cmd_trace('creating bridge')
            bridge = get_bridge(self.window)
            _cmd_trace('bridge created type=%s', type(bridge).__name__)
        except Exception as exc:
            _cmd_trace('bridge creation failed error=%s', exc)
            _display_assistant_response(
                self.window,
                prompt,
                {
                    'msg': {
                        'type': 'error',
                        'text': f'Failed to initialize Codex bridge: {exc}',
                    }
                },
                session_id,
            )
            logger.exception('Failed to create Codex bridge')
            return
        project_data = self.window.project_data() or {}
        settings_block = project_data.get('settings') or {}
        codex_cfg = settings_block.get('codex') or {}
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
        _cmd_trace('bridge.send dispatched')

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
        _cmd_trace('user prompt echoed to transcript')


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
            view.assign_syntax(_markdown_syntax_resource())
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

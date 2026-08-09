from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch


sublime = sys.modules.setdefault('sublime', types.ModuleType('sublime'))
sublime.Window = getattr(sublime, 'Window', object)
sublime.View = getattr(sublime, 'View', object)

if 'sublime_plugin' not in sys.modules:
    sublime_plugin = types.ModuleType('sublime_plugin')
    sublime_plugin.EventListener = object
    sublime_plugin.WindowCommand = object
    sublime_plugin.TextCommand = object
    sys.modules['sublime_plugin'] = sublime_plugin

from plugin import commands


class FakeWindow:
    def __init__(self, window_id: int = 7) -> None:
        self._window_id = window_id

    def id(self) -> int:
        return self._window_id


class FakeView:
    def __init__(self, operations: list[tuple]) -> None:
        self.operations = operations

    def set_read_only(self, value: bool) -> None:
        self.operations.append(('read_only', value))

    def run_command(self, command: str, args: dict) -> None:
        if command == 'append':
            self.operations.append(('append', args['characters']))

    def size(self) -> int:
        return 0

    def sel(self) -> list:
        return []

    def show(self, point: int) -> None:
        self.operations.append(('show', point))


class TranscriptSteeringOrderTests(unittest.TestCase):
    def tearDown(self) -> None:
        commands.STREAMING_AGENT_BLOCKS.clear()
        commands.PENDING_USER_INPUTS.clear()

    def test_user_input_waits_while_agent_message_is_streaming(self) -> None:
        window = FakeWindow()
        commands.STREAMING_AGENT_BLOCKS[(window.id(), 'agent-1')] = {'text': 'partial'}

        with patch('plugin.commands._display_assistant_response') as display:
            commands._display_or_queue_user_input(window, 'steer me', 'session-1')

            display.assert_not_called()
            self.assertEqual(
                commands.PENDING_USER_INPUTS[window.id()],
                [('steer me', 'session-1')],
            )

            commands.STREAMING_AGENT_BLOCKS.clear()
            commands._flush_pending_user_inputs(window)

        display.assert_called_once_with(
            window,
            'steer me',
            commands._user_input_event('steer me'),
            'session-1',
        )

    def test_finishing_agent_block_flushes_user_input_after_its_text(self) -> None:
        window = FakeWindow()
        operations: list[tuple] = []
        view = FakeView(operations)
        commands.STREAMING_AGENT_BLOCKS[(window.id(), 'agent-1')] = {
            'text': 'finished answer',
            'started': '1',
        }
        commands.PENDING_USER_INPUTS[window.id()] = [('steer me', 'session-1')]

        def append_section(_view, header, body=''):
            operations.append(('section', header, body))
            return 0

        with (
            patch('plugin.commands._get_transcript_view', return_value=view),
            patch('plugin.commands.apply_presentation'),
            patch('plugin.commands._markdown_syntax_resource', return_value='syntax'),
            patch('plugin.commands.append_markdown_section', side_effect=append_section),
            patch('plugin.commands._get_fold_section_names', return_value=set()),
        ):
            commands._display_assistant_response(
                window,
                '',
                {
                    'id': 'agent-1',
                    'msg': {'type': 'agent_message', 'text': 'finished answer'},
                },
                'session-1',
            )

        close_index = operations.index(('append', '\n\n'))
        user_index = operations.index(('section', '## user_input\n\n', 'steer me\n\n'))
        self.assertLess(close_index, user_index)
        self.assertNotIn((window.id(), 'agent-1'), commands.STREAMING_AGENT_BLOCKS)
        self.assertNotIn(window.id(), commands.PENDING_USER_INPUTS)

    def test_terminal_event_flushes_user_input_at_the_event_boundary(self) -> None:
        window = FakeWindow()
        operations: list[tuple] = []
        view = FakeView(operations)
        commands.STREAMING_AGENT_BLOCKS[(window.id(), 'agent-1')] = {
            'text': 'partial answer',
            'started': '1',
        }
        commands.PENDING_USER_INPUTS[window.id()] = [('steer me', 'session-1')]

        def append_section(_view, header, body=''):
            operations.append(('section', header, body))
            return 0

        with (
            patch('plugin.commands._get_transcript_view', return_value=view),
            patch('plugin.commands.apply_presentation'),
            patch('plugin.commands._markdown_syntax_resource', return_value='syntax'),
            patch('plugin.commands.append_markdown_section', side_effect=append_section),
            patch('plugin.commands._get_fold_section_names', return_value=set()),
        ):
            commands._display_assistant_response(
                window,
                '',
                {'msg': {'type': 'error', 'message': 'steer failed'}},
                'session-1',
            )

        close_index = operations.index(('append', '\n\n'))
        user_index = operations.index(('section', '## user_input\n\n', 'steer me\n\n'))
        error_index = operations.index(('section', '### Error\n\n', 'steer failed\n\n'))
        self.assertLess(close_index, user_index)
        self.assertLess(user_index, error_index)


if __name__ == '__main__':
    unittest.main()

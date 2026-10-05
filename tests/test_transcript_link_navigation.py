from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch


sublime = sys.modules.setdefault('sublime', types.ModuleType('sublime'))
sublime.Window = getattr(sublime, 'Window', object)
sublime.View = getattr(sublime, 'View', object)
sublime.ENCODED_POSITION = 1
sublime.FORCE_GROUP = 8
sublime.ADD_TO_SELECTION = 32

if 'sublime_plugin' not in sys.modules:
    sublime_plugin = types.ModuleType('sublime_plugin')
    sublime_plugin.EventListener = object
    sublime_plugin.WindowCommand = object
    sublime_plugin.TextCommand = object
    sys.modules['sublime_plugin'] = sublime_plugin

from plugin.chat_syntax import TRANSCRIPT_VIEW_FLAG
from plugin.commands import (
    CodexInputPanelEventListener,
    CodexStopExecutionCommand,
    CodexTurnRunningContextEventListener,
)
from plugin.vendor.sublime_chat_ui.links import MarkdownLink


class FakeSettings:
    def get(self, key, default=None):
        return key == TRANSCRIPT_VIEW_FLAG or default


class FakeLine:
    def begin(self) -> int:
        return 0


class FakeWindow:
    def __init__(self) -> None:
        self.opened = []

    def get_view_index(self, _view):
        return 1, 0

    def open_file(self, target, flags, group):
        self.opened.append((target, flags, group))


class FakeView:
    def __init__(self, window: FakeWindow) -> None:
        self._window = window

    def settings(self) -> FakeSettings:
        return FakeSettings()

    def window_to_text(self, _coordinates) -> int:
        return 3

    def score_selector(self, _point, _selector) -> int:
        return 1

    def line(self, _point) -> FakeLine:
        return FakeLine()

    def substr(self, _line) -> str:
        return '[source](/workspace/source.py:12)'

    def window(self) -> FakeWindow:
        return self._window


class TranscriptLinkNavigationTests(unittest.TestCase):
    def test_reuses_file_open_in_another_group(self) -> None:
        window = FakeWindow()
        view = FakeView(window)
        listener = CodexInputPanelEventListener()

        with (
            patch(
                'plugin.commands.markdown_link_at',
                return_value=MarkdownLink('/workspace/source.py:12', 0, 33),
            ),
            patch('plugin.commands.local_file_target', return_value='/workspace/source.py:12'),
        ):
            result = listener._open_transcript_link(
                view,
                'drag_select',
                {'event': {'x': 1, 'y': 2}},
            )

        self.assertEqual(result, ('noop', None))
        self.assertEqual(window.opened, [(
            '/workspace/source.py:12', sublime.ENCODED_POSITION, 1,
        )])
        self.assertFalse(window.opened[0][1] & sublime.FORCE_GROUP)


class StopExecutionTests(unittest.TestCase):
    def test_running_context_uses_only_active_windows_bridge(self) -> None:
        window = FakeWindow()
        view = FakeView(window)
        bridge = types.SimpleNamespace(is_turn_active=lambda: True)

        with patch('plugin.commands.get_existing_bridge', return_value=bridge) as lookup:
            result = CodexTurnRunningContextEventListener().on_query_context(
                view, 'codex_turn_running', 0, True, False,
            )

        self.assertTrue(result)
        lookup.assert_called_once_with(window)

    def test_stop_command_interrupts_only_active_windows_bridge(self) -> None:
        window = FakeWindow()
        view = FakeView(window)
        bridge = types.SimpleNamespace(interrupt_active_turn=lambda: True)
        command = object.__new__(CodexStopExecutionCommand)
        command.view = view

        with (
            patch('plugin.commands.get_existing_bridge', return_value=bridge) as lookup,
            patch('plugin.commands.sublime.status_message', create=True),
        ):
            command.run(None)

        lookup.assert_called_once_with(window)


if __name__ == '__main__':
    unittest.main()

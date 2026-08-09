from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import call, patch


sublime = sys.modules.setdefault('sublime', types.ModuleType('sublime'))
sublime.Window = getattr(sublime, 'Window', object)
sublime.View = getattr(sublime, 'View', object)

if 'sublime_plugin' not in sys.modules:
    sublime_plugin = types.ModuleType('sublime_plugin')
    sublime_plugin.EventListener = object
    sublime_plugin.WindowCommand = object
    sublime_plugin.TextCommand = object
    sys.modules['sublime_plugin'] = sublime_plugin

from plugin import commands, lifecycle
from plugin.chat_syntax import TRANSCRIPT_VIEW_FLAG


class FakeRegion:
    def __init__(self, start: int, end: int | None = None) -> None:
        self._start = start
        self._end = start if end is None else end

    def begin(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def contains(self, point: int) -> bool:
        return self._start <= point <= self._end


class FakeSettings:
    def __init__(self, transcript: bool = False) -> None:
        self.transcript = transcript

    def get(self, key: str, default=None):
        if key == TRANSCRIPT_VIEW_FLAG:
            return self.transcript
        return default


class FakeView:
    def __init__(self, text: str, sections: list[FakeRegion], transcript: bool = False) -> None:
        self.text = text
        self.sections = sections
        self.folded: list[FakeRegion] = []
        self.unfolded: list[FakeRegion] = []
        self._settings = FakeSettings(transcript)

    def settings(self) -> FakeSettings:
        return self._settings

    def is_loading(self) -> bool:
        return False

    def find_by_selector(self, selector: str) -> list[FakeRegion]:
        self.assert_selector = selector
        return list(self.sections)

    def line(self, point: int) -> FakeRegion:
        start = self.text.rfind('\n', 0, point) + 1
        end = self.text.find('\n', point)
        return FakeRegion(start, len(self.text) if end < 0 else end)

    def substr(self, region: FakeRegion) -> str:
        return self.text[region.begin():region.end()]

    def fold(self, region: FakeRegion) -> None:
        self.folded.append(region)

    def unfold(self, region: FakeRegion) -> None:
        self.unfolded.append(region)


class FakeWindow:
    def __init__(self, views: list[FakeView], panel: FakeView | None = None) -> None:
        self._views = views
        self.panel = panel

    def views(self) -> list[FakeView]:
        return self._views

    def find_output_panel(self, name: str) -> FakeView | None:
        return self.panel if name == 'codex' else None


class TranscriptFoldingTests(unittest.TestCase):
    def test_nested_markdown_headings_do_not_end_transcript_sections(self) -> None:
        syntax = Path(__file__).parents[1] / (
            'plugin/vendor/sublime_chat_ui/Syntaxes/ChatMarkdown.sublime-syntax'
        )
        source = syntax.read_text(encoding='utf-8')

        self.assertEqual(source.count("- match: '^(?={{chat_section_break}})'"), 6)
        self.assertNotIn("- match: '^(?={{atx_heading}})'", source)

    def test_restores_only_sections_named_in_fold_settings(self) -> None:
        text = (
            '## agent_message\n\nAnswer\n\n'
            '----------\n\n'
            '## user_input\n\nPrompt\n'
        )
        agent_body_start = text.index('\n', text.index('## agent_message'))
        agent_body_end = text.index('----------')
        user_header_start = text.index('## user_input')
        user_body_start = text.index('\n', user_header_start)
        view = FakeView(
            text,
            [
                FakeRegion(agent_body_start, agent_body_end),
                FakeRegion(user_body_start, len(text)),
            ],
        )

        with (
            patch('plugin.commands.sublime.Region', FakeRegion, create=True),
            patch('plugin.commands._get_fold_section_names', return_value={'agent_message'}),
        ):
            commands.restore_configured_folds(object(), view)

        self.assertEqual(len(view.folded), 1)
        self.assertEqual(view.unfolded, view.folded)
        self.assertEqual(view.folded[0].begin(), agent_body_start)
        self.assertEqual(view.folded[0].end(), user_header_start - 1)
        self.assertEqual(
            view.substr(view.folded[0]),
            '\n\nAnswer\n\n----------\n',
        )

    def test_startup_restores_transcript_and_output_panel(self) -> None:
        transcript = FakeView('', [], transcript=True)
        ordinary = FakeView('', [])
        panel = FakeView('', [])
        window = FakeWindow([transcript, ordinary], panel)

        with (
            patch('plugin.lifecycle.sublime.windows', return_value=[window], create=True),
            patch('plugin.lifecycle.migrate_chat_syntax', return_value=False),
            patch('plugin.lifecycle.restore_configured_folds') as restore,
        ):
            lifecycle._migrate_open_chat_views()

        self.assertEqual(restore.call_args_list, [call(window, transcript), call(window, panel)])

    def test_changed_settings_resynchronize_open_transcript_sections(self) -> None:
        transcript = FakeView('', [], transcript=True)
        ordinary = FakeView('', [])
        panel = FakeView('', [])
        window = FakeWindow([transcript, ordinary], panel)

        with (
            patch('plugin.lifecycle.sublime.windows', return_value=[window], create=True),
            patch('plugin.lifecycle.sync_configured_folds') as sync,
        ):
            lifecycle._sync_open_chat_folds()

        self.assertEqual(sync.call_args_list, [call(window, transcript), call(window, panel)])

    def test_sync_unfolds_removed_sections_and_folds_newly_configured_sections(self) -> None:
        text = (
            '## Tool call\n\nPayload\n\n'
            '----------\n\n'
            '## agent_message\n\nAnswer\n'
        )
        tool_body_start = text.index('\n', text.index('## Tool call'))
        tool_body_end = text.index('----------')
        agent_header_start = text.index('## agent_message')
        agent_body_start = text.index('\n', agent_header_start)
        view = FakeView(
            text,
            [
                FakeRegion(tool_body_start, tool_body_end),
                FakeRegion(agent_body_start, len(text)),
            ],
        )

        with (
            patch('plugin.commands.sublime.Region', FakeRegion, create=True),
            patch('plugin.commands._get_fold_section_names', return_value={'tool call'}),
        ):
            commands.sync_configured_folds(object(), view)

        self.assertEqual(len(view.unfolded), 2)
        self.assertEqual(view.folded, [view.unfolded[0]])

    def test_refolds_configured_section_after_headerless_continuation(self) -> None:
        text = '### Tool call\n\nRequest\n\nResult appended later\n\n'
        body_start = text.index('\n', text.index('### Tool call'))
        append_start = text.index('Result appended later')
        section = FakeRegion(body_start, len(text))
        view = FakeView(text, [section])

        with (
            patch('plugin.commands.sublime.Region', FakeRegion, create=True),
            patch('plugin.commands._get_fold_section_names', return_value={'tool call'}),
        ):
            result = commands._refold_configured_continuation(
                object(),
                view,
                [section],
                append_start,
            )

        self.assertTrue(result)
        self.assertEqual(len(view.folded), 1)
        self.assertEqual(view.folded, view.unfolded)
        self.assertIn('Result appended later', view.substr(view.folded[0]))

    def test_does_not_refold_unconfigured_continuation(self) -> None:
        text = '### Tool call\n\nRequest\n\nResult\n'
        body_start = text.index('\n', text.index('### Tool call'))
        append_start = text.index('Result')
        section = FakeRegion(body_start, len(text))
        view = FakeView(text, [section])

        with patch('plugin.commands._get_fold_section_names', return_value={'command output'}):
            result = commands._refold_configured_continuation(
                object(),
                view,
                [section],
                append_start,
            )

        self.assertFalse(result)
        self.assertEqual(view.folded, [])


if __name__ == '__main__':
    unittest.main()

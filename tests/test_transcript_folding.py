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
from plugin.transcript import (
    TranscriptDocument,
    TranscriptItemKey,
    TranscriptItemKind,
    TranscriptMutation,
    TranscriptMutationKind,
    parse_blocks,
    serialize_block,
)
from plugin.transcript_runtime import FoldController, apply_mutation, clear_sessions, reconcile_folds


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

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, FakeRegion)
            and self.begin() == other.begin()
            and self.end() == other.end()
        )


class FakeSettings:
    def __init__(self, transcript: bool = False) -> None:
        self.transcript = transcript

    def get(self, key: str, default=None):
        if key == TRANSCRIPT_VIEW_FLAG:
            return self.transcript
        return default


class FakeView:
    _next_id = 1

    def __init__(self, text: str, transcript: bool = False) -> None:
        self.text = text
        self.folded: list[FakeRegion] = []
        self.unfolded: list[FakeRegion] = []
        self._settings = FakeSettings(transcript)
        self._id = FakeView._next_id
        FakeView._next_id += 1
        self._window = None
        self._read_only = True
        self.post_command = None

    def settings(self) -> FakeSettings:
        return self._settings

    def id(self) -> int:
        return self._id

    def buffer_id(self) -> int:
        return self._id

    def window(self):
        return self._window

    def size(self) -> int:
        return len(self.text)

    def is_loading(self) -> bool:
        return False

    def is_read_only(self) -> bool:
        return self._read_only

    def set_read_only(self, value: bool) -> None:
        self._read_only = value

    def run_command(self, command: str, args: dict) -> None:
        if command != 'codex_apply_transcript_edit':
            raise AssertionError(f'Unexpected command: {command}')
        self.text = self.text[:args['begin']] + args['text'] + self.text[args['end']:]
        if self.post_command is not None:
            self.post_command()

    def substr(self, region) -> str:
        if isinstance(region, int):
            return self.text[region]
        return self.text[region.begin():region.end()]

    def fold(self, region: FakeRegion) -> None:
        if region not in self.folded:
            self.folded.append(region)

    def unfold(self, region: FakeRegion) -> None:
        self.unfolded.append(region)
        self.folded = [candidate for candidate in self.folded if candidate != region]

    def is_folded(self, region: FakeRegion) -> bool:
        return region in self.folded


class FakeWindow:
    def __init__(self, views: list[FakeView], panel: FakeView | None = None) -> None:
        self._views = views
        self.panel = panel
        for view in views:
            view._window = self
        if panel is not None:
            panel._window = self

    def views(self) -> list[FakeView]:
        return self._views

    def find_output_panel(self, name: str) -> FakeView | None:
        return self.panel if name == 'codex' else None


class TranscriptFoldingTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_sessions()

    def test_nested_markdown_headings_do_not_end_transcript_sections(self) -> None:
        syntax = Path(__file__).parents[1] / (
            'plugin/vendor/sublime_chat_ui/Syntaxes/ChatMarkdown.sublime-syntax'
        )
        source = syntax.read_text(encoding='utf-8')

        self.assertEqual(source.count("- match: '^(?={{chat_section_break}})'"), 6)
        self.assertNotIn("- match: '^(?={{atx_heading}})'", source)

    def test_parser_ignores_nested_headings_and_separators_inside_fences(self) -> None:
        text = (
            '----------\n\n### Tool call\n\n'
            '```text\n----------\n### not a block\n```\n\n'
            '### nested heading\n\n'
            '----------\n\n## agent_message\n\nAnswer\n\n'
        )

        blocks = parse_blocks(text)

        self.assertEqual([block.title for block in blocks], ['Tool call', 'agent_message'])
        self.assertIn('### nested heading', blocks[0].text)

    def test_serializer_keeps_separator_outside_fold_and_leaves_guard_newline(self) -> None:
        rendered = serialize_block('### Command Output\n\n', 'stdout\n')
        fold = rendered.layout.fold

        self.assertIsNotNone(fold)
        assert fold is not None
        self.assertGreaterEqual(fold.begin, rendered.layout.separator.end)
        self.assertEqual(fold.end, rendered.layout.guard.begin)
        self.assertEqual(rendered.text[rendered.layout.guard.begin:], '\n')

    def test_restore_folds_explicit_body_without_separator_tail(self) -> None:
        text = (
            '----------\n\n### Command Output\n\nstdout\n\n'
            '----------\n\n## agent_message\n\nAnswer\n\n'
        )
        view = FakeView(text)

        with (
            patch('plugin.transcript_runtime.sublime.Region', FakeRegion, create=True),
            patch('plugin.commands._get_fold_section_names', return_value={'command output'}),
        ):
            commands.restore_configured_folds(object(), view)

        self.assertEqual(len(view.folded), 1)
        folded = view.folded[0]
        self.assertNotIn('----------', view.substr(folded))
        self.assertEqual(text[folded.end()], '\n')
        next_separator = text.index('----------', folded.end())
        self.assertGreater(next_separator, folded.end())

    def test_startup_restores_transcript_and_output_panel(self) -> None:
        transcript = FakeView('', transcript=True)
        ordinary = FakeView('')
        panel = FakeView('')
        window = FakeWindow([transcript, ordinary], panel)

        with (
            patch('plugin.lifecycle.sublime.windows', return_value=[window], create=True),
            patch('plugin.lifecycle.migrate_chat_syntax', return_value=False),
            patch('plugin.lifecycle.restore_configured_folds') as restore,
        ):
            lifecycle._migrate_open_chat_views()

        self.assertEqual(restore.call_args_list, [call(window, transcript), call(window, panel)])

    def test_changed_settings_resynchronize_open_transcript_sections(self) -> None:
        transcript = FakeView('', transcript=True)
        ordinary = FakeView('')
        panel = FakeView('')
        window = FakeWindow([transcript, ordinary], panel)

        with (
            patch('plugin.lifecycle.sublime.windows', return_value=[window], create=True),
            patch('plugin.lifecycle.sync_configured_folds') as sync,
        ):
            lifecycle._sync_open_chat_folds()

        self.assertEqual(sync.call_args_list, [call(window, transcript), call(window, panel)])

    def test_manual_open_override_survives_policy_reapplication(self) -> None:
        text = '----------\n\n### Tool call\n\nPayload\n\n'
        block = parse_blocks(text)[0]
        view = FakeView(text)
        controller = FoldController()

        with patch('plugin.transcript_runtime.sublime.Region', FakeRegion, create=True):
            controller.apply(view, [block], {'tool call'})
            view.folded.clear()  # user unfolds through Sublime
            controller.reconcile(view, [block])
            controller.apply(view, [block], {'tool call'})

        self.assertEqual(view.folded, [])

    def test_started_and_completed_share_one_typed_block(self) -> None:
        document = TranscriptDocument()
        key = TranscriptItemKey(conversation_id='thread', item_id='call-1')
        started = TranscriptMutation(
            kind=TranscriptMutationKind.CREATE,
            key=key,
            item_kind=TranscriptItemKind.COMMAND_EXECUTION,
            header='### Command Call\n\n',
            body='```bash\necho ok\n```\n\n',
        )
        completed = TranscriptMutation(
            kind=TranscriptMutationKind.FINALIZE,
            key=key,
            item_kind=TranscriptItemKind.COMMAND_EXECUTION,
            header='### Command Output\n\n',
            body='```\nok\n```\n\n',
        )

        document.reduce(started, buffer_ends_with_newline=True)
        edit = document.reduce(completed, buffer_ends_with_newline=True)

        self.assertIsNotNone(edit)
        self.assertEqual(len(document.blocks), 1)
        self.assertEqual(document.blocks[0].title, 'Command Output')
        self.assertIn('echo ok', document.blocks[0].text)
        self.assertIn('\nok\n', document.blocks[0].text)

    def test_out_of_order_completion_does_not_reorder_blocks(self) -> None:
        document = TranscriptDocument()
        keys = [
            TranscriptItemKey(conversation_id='thread', item_id=item_id)
            for item_id in ('a', 'b')
        ]
        for key in keys:
            document.reduce(
                TranscriptMutation(
                    kind=TranscriptMutationKind.CREATE,
                    key=key,
                    item_kind=TranscriptItemKind.MCP_TOOL_CALL,
                    header='### Tool call\n\n',
                    body=key.item_id,
                ),
                buffer_ends_with_newline=True,
            )

        for key in reversed(keys):
            document.reduce(
                TranscriptMutation(
                    kind=TranscriptMutationKind.FINALIZE,
                    key=key,
                    item_kind=TranscriptItemKind.MCP_TOOL_CALL,
                    header=None,
                    body=' done',
                ),
                buffer_ends_with_newline=True,
            )

        self.assertEqual([block.key for block in document.blocks], keys)

    def test_runtime_replaces_live_tool_block_and_folds_only_its_body(self) -> None:
        view = FakeView('')
        FakeWindow([view])
        key = TranscriptItemKey(conversation_id='thread', item_id='call-1')
        started = TranscriptMutation(
            kind=TranscriptMutationKind.CREATE,
            key=key,
            item_kind=TranscriptItemKind.COMMAND_EXECUTION,
            header='### Command Call\n\n',
            body='```bash\necho ok\n```\n\n',
        )
        completed = TranscriptMutation(
            kind=TranscriptMutationKind.FINALIZE,
            key=key,
            item_kind=TranscriptItemKind.COMMAND_EXECUTION,
            header='### Command Output\n\n',
            body='```\nok\n```\n\n',
        )
        view.post_command = lambda: reconcile_folds(view)

        with (
            patch('plugin.transcript_runtime.sublime.Region', FakeRegion, create=True),
            patch('plugin.transcript_runtime.sublime.windows', return_value=[], create=True),
        ):
            fold_names = {'command call', 'command output'}
            apply_mutation(view, started, fold_names)
            final_block = apply_mutation(view, completed, fold_names)

        self.assertEqual(view.text.count('----------'), 1)
        self.assertEqual(view.text.count('### Command Output'), 1)
        self.assertNotIn('### Command Call', view.text)
        self.assertEqual(len(view.folded), 1)
        folded = view.folded[0]
        self.assertNotIn('----------', view.substr(folded))
        self.assertEqual(view.text[folded.end()], '\n')
        self.assertEqual(final_block.fold_span.end, folded.end())


if __name__ == '__main__':
    unittest.main()

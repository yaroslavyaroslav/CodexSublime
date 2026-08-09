import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest


class Region:
    def __init__(self, begin: int, end: int) -> None:
        self.begin = begin
        self.end = end


class Phantom:
    def __init__(self, region: Region, content: str, layout: int) -> None:
        self.region = region
        self.content = content
        self.layout = layout


class MarkdownPhantom(Phantom):
    def __init__(self, region, content, layout, css=None, wrapper_class=None) -> None:
        super().__init__(region, content, layout)
        self.css = css
        self.wrapper_class = wrapper_class


class PhantomSet:
    def __init__(self, view: 'FakeView', key: str) -> None:
        self.view = view
        self.key = key
        self.phantoms = []

    def update(self, phantoms: list[Phantom]) -> None:
        self.phantoms = phantoms


fake_sublime = types.ModuleType('sublime')
fake_sublime.Region = Region
fake_sublime.Phantom = Phantom
fake_sublime.PhantomSet = PhantomSet
fake_sublime.PhantomLayout = types.SimpleNamespace(BLOCK=7)
fake_sublime.set_timeout = lambda callback, _delay=0: callback()
sys.modules['sublime'] = fake_sublime

from runtime import SublimeToolRuntime  # noqa: E402


class FakeView:
    _next_id = 1

    def __init__(self, path: str) -> None:
        self._path = path
        self._id = FakeView._next_id
        FakeView._next_id += 1
        self.reference = None
        self._change_count = 3
        self.dirty = False
        self.closed = False
        self.group = 0

    def file_name(self) -> str:
        return self._path

    def id(self) -> int:
        return self._id

    def name(self) -> str:
        return os.path.basename(self._path)

    def is_loading(self) -> bool:
        return False

    def is_dirty(self) -> bool:
        return self.dirty

    def close(self) -> bool:
        self.closed = True
        return True

    def set_reference_document(self, text: str) -> None:
        self.reference = text

    def change_count(self) -> int:
        return self._change_count

    def text_point(self, row: int, column: int) -> int:
        return row * 1000 + column


class FakeWindow:
    def __init__(self, root: str) -> None:
        self.root = root
        self._views: list[FakeView] = []
        self.focused = None

    def folders(self) -> list[str]:
        return [self.root]

    def open_file(self, path: str) -> FakeView:
        view = next((item for item in self._views if item.file_name() == path), None)
        if view is None:
            view = FakeView(path)
            self._views.append(view)
        return view

    def views(self) -> list[FakeView]:
        return [view for view in self._views if not view.closed]

    def focus_view(self, view: FakeView) -> None:
        self.focused = view

    def active_view(self) -> FakeView | None:
        return self.focused or (self.views()[0] if self.views() else None)

    def get_view_index(self, view: FakeView) -> tuple[int, int]:
        group_views = [candidate for candidate in self.views() if candidate.group == view.group]
        return view.group, group_views.index(view)


def git(root: str, *args: str) -> str:
    result = subprocess.run(
        ['git', *args], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class SublimeToolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Test')
        git(self.root, 'config', 'user.email', 'test@example.com')
        Path(self.root, 'modified.py').write_text('one\ntwo\nthree\n')
        Path(self.root, 'deleted.py').write_text('gone\n')
        Path(self.root, 'renamed_old.py').write_text('alpha\nbeta\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'base')
        self.base = git(self.root, 'rev-parse', 'HEAD')

        Path(self.root, 'modified.py').write_text('one\nchanged\nthree\n')
        Path(self.root, 'added.py').write_text('new\nfile\n')
        os.unlink(Path(self.root, 'deleted.py'))
        git(self.root, 'mv', 'renamed_old.py', 'renamed.py')
        Path(self.root, 'renamed.py').write_text('alpha\nbeta changed\n')
        git(self.root, 'add', '-A')

        self.window = FakeWindow(self.root)
        self.runtime = SublimeToolRuntime(self.window, self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_opens_current_side_diffs_and_ignores_deletions(self) -> None:
        responses = []
        self.runtime._open_diff({'base_ref': self.base, 'comparison': 'direct'}, responses.append)

        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0].success, responses[0].text)
        payload = json.loads(responses[0].text)
        opened = {item['path']: item for item in payload['opened']}
        self.assertEqual(set(opened), {'added.py', 'modified.py', 'renamed.py'})
        self.assertNotIn('deleted.py', opened)
        self.assertEqual(self.window.views()[0].reference, '')
        modified = opened['modified.py']
        self.assertEqual(modified['annotatable_lines'], [2])

    def test_annotations_are_limited_to_changed_lines_and_fresh_buffer(self) -> None:
        responses = []
        self.runtime._open_diff({'base_ref': self.base, 'comparison': 'direct'}, responses.append)
        opened = {item['path']: item for item in json.loads(responses[0].text)['opened']}
        change_count = opened['modified.py']['change_count']

        result = self.runtime._set_annotations({
            'files': [{
                'path': 'modified.py', 'expected_change_count': change_count,
                'annotations': [{'id': 'intent', 'line': 2, 'markdown': '**Reason**'}],
            }],
        })
        self.assertTrue(result.success, result.text)
        self.assertEqual(json.loads(result.text)['count'], 1)
        phantom = next(iter(self.runtime._annotation_sets.values())).phantoms[0]
        self.assertIn('border: 1px solid', phantom.content)
        self.assertIn('class="sublime-agent-annotation"', phantom.content)

        deleted_side = self.runtime._set_annotations({
            'files': [{
                'path': 'modified.py',
                'annotations': [{'id': 'wrong', 'line': 1, 'markdown': 'Not changed'}],
            }],
        })
        self.assertFalse(deleted_side.success)
        self.assertIn('not an added or changed line', deleted_side.text)

        stale = self.runtime._set_annotations({
            'files': [{
                'path': 'modified.py', 'expected_change_count': change_count + 1,
                'annotations': [{'id': 'intent', 'line': 2, 'markdown': 'Reason'}],
            }],
        })
        self.assertFalse(stale.success)
        self.assertIn('stale buffer', stale.text)

        cleared = self.runtime._clear_annotations({'path': 'modified.py'})
        self.assertTrue(cleared.success)
        self.assertEqual(json.loads(cleared.text)['cleared_sets'], 1)

    def test_annotation_batch_validates_every_file_before_updating_phantoms(self) -> None:
        responses = []
        self.runtime._open_diff({'base_ref': self.base, 'comparison': 'direct'}, responses.append)
        opened = {item['path']: item for item in json.loads(responses[0].text)['opened']}

        invalid = self.runtime._set_annotations({
            'files': [
                {
                    'path': 'modified.py',
                    'expected_change_count': opened['modified.py']['change_count'],
                    'annotations': [{'id': 'modified', 'line': 2, 'markdown': 'Modified'}],
                },
                {
                    'path': 'added.py',
                    'expected_change_count': opened['added.py']['change_count'],
                    'annotations': [{'id': 'added', 'line': 99, 'markdown': 'Added'}],
                },
            ],
        })

        self.assertFalse(invalid.success)
        self.assertEqual(self.runtime._annotation_sets, {})
        error = json.loads(invalid.text)
        self.assertEqual(error['files'][0]['path'], 'added.py')

        valid = self.runtime._set_annotations({
            'files': [
                {
                    'path': 'modified.py',
                    'expected_change_count': opened['modified.py']['change_count'],
                    'annotations': [{'id': 'modified', 'line': 2, 'markdown': 'Modified'}],
                },
                {
                    'path': 'added.py',
                    'expected_change_count': opened['added.py']['change_count'],
                    'annotations': [{'id': 'added', 'line': 1, 'markdown': 'Added'}],
                },
            ],
        })

        self.assertTrue(valid.success, valid.text)
        payload = json.loads(valid.text)
        self.assertEqual(payload['count'], 2)
        self.assertEqual([item['path'] for item in payload['results']], ['modified.py', 'added.py'])
        self.assertEqual(len(self.runtime._annotation_sets), 2)

    def test_annotation_batch_rejects_the_removed_single_file_contract(self) -> None:
        result = self.runtime._set_annotations({
            'path': 'modified.py',
            'annotations': [{'id': 'intent', 'line': 2, 'markdown': 'Reason'}],
        })

        self.assertFalse(result.success)
        self.assertIn('files must be a non-empty array', result.text)

    def test_mdpopups_annotations_receive_the_framed_wrapper(self) -> None:
        responses = []
        self.runtime._open_diff({'base_ref': self.base, 'comparison': 'direct'}, responses.append)
        self.runtime._uses_mdpopups = True
        self.runtime._phantom_cls = MarkdownPhantom

        result = self.runtime._set_annotations({
            'files': [{
                'path': 'modified.py',
                'annotations': [{'id': 'intent', 'line': 2, 'markdown': '**Reason**'}],
            }],
        })

        self.assertTrue(result.success, result.text)
        phantom = next(iter(self.runtime._annotation_sets.values())).phantoms[0]
        self.assertEqual(phantom.wrapper_class, 'sublime-agent-annotation')
        self.assertIn('border: 1px solid', phantom.css)

    def test_closes_only_clean_views_opened_by_this_runtime(self) -> None:
        preexisting = self.window.open_file(str(Path(self.root, 'modified.py')))
        responses = []
        self.runtime._open_diff({'base_ref': self.base, 'comparison': 'direct'}, responses.append)
        added = next(view for view in self.window.views() if view.file_name().endswith('added.py'))
        added.dirty = True

        result = self.runtime._close_views({})

        self.assertTrue(result.success, result.text)
        payload = json.loads(result.text)
        self.assertEqual(payload['closed'], ['renamed.py'])
        self.assertEqual(payload['skipped'], [
            {'path': 'added.py', 'reason': 'view has unsaved changes'},
        ])
        self.assertIn(preexisting, self.window.views())
        self.assertIn(added, self.window.views())

        added.dirty = False
        targeted = self.runtime._close_views({'paths': ['added.py']})
        self.assertTrue(targeted.success, targeted.text)
        self.assertEqual(json.loads(targeted.text)['closed'], ['added.py'])

    def test_lists_open_views_with_group_index_and_state(self) -> None:
        first = self.window.open_file(str(Path(self.root, 'modified.py')))
        second = self.window.open_file(str(Path(self.root, 'added.py')))
        second.group = 1
        second.dirty = True
        self.window.focus_view(second)
        self.runtime._opened_view_ids.add(second.id())

        result = self.runtime._list_views()

        self.assertTrue(result.success, result.text)
        payload = json.loads(result.text)
        self.assertEqual(payload['count'], 2)
        listed = {item['view_id']: item for item in payload['views']}
        self.assertEqual(listed[first.id()]['group'], 0)
        self.assertEqual(listed[first.id()]['index'], 0)
        self.assertFalse(listed[first.id()]['active'])
        self.assertEqual(listed[second.id()]['group'], 1)
        self.assertEqual(listed[second.id()]['index'], 0)
        self.assertTrue(listed[second.id()]['active'])
        self.assertTrue(listed[second.id()]['dirty'])
        self.assertTrue(listed[second.id()]['runtime_opened'])

    def test_closes_explicit_preexisting_views_by_id_and_skips_dirty_views(self) -> None:
        clean = self.window.open_file(str(Path(self.root, 'modified.py')))
        dirty = self.window.open_file(str(Path(self.root, 'added.py')))
        dirty.dirty = True

        result = self.runtime._close_views({'view_ids': [clean.id(), dirty.id(), 99999]})

        self.assertTrue(result.success, result.text)
        payload = json.loads(result.text)
        self.assertEqual(payload['closed'], ['modified.py'])
        self.assertEqual(payload['closed_view_ids'], [clean.id()])
        self.assertEqual(payload['skipped'], [
            {'path': 'added.py', 'reason': 'view has unsaved changes'},
        ])
        self.assertEqual(payload['not_found']['view_ids'], [99999])
        self.assertNotIn(clean, self.window.views())
        self.assertIn(dirty, self.window.views())


if __name__ == '__main__':
    unittest.main()

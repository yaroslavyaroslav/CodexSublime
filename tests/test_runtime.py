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

    def file_name(self) -> str:
        return self._path

    def id(self) -> int:
        return self._id

    def is_loading(self) -> bool:
        return False

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
        return list(self._views)

    def focus_view(self, view: FakeView) -> None:
        self.focused = view


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
            'path': 'modified.py', 'expected_change_count': change_count,
            'annotations': [{'id': 'intent', 'line': 2, 'markdown': '**Reason**'}],
        })
        self.assertTrue(result.success, result.text)
        self.assertEqual(json.loads(result.text)['count'], 1)
        phantom = next(iter(self.runtime._annotation_sets.values())).phantoms[0]
        self.assertIn('border: 1px solid', phantom.content)
        self.assertIn('class="sublime-agent-annotation"', phantom.content)

        deleted_side = self.runtime._set_annotations({
            'path': 'modified.py',
            'annotations': [{'id': 'wrong', 'line': 1, 'markdown': 'Not changed'}],
        })
        self.assertFalse(deleted_side.success)
        self.assertIn('not an added or changed line', deleted_side.text)

        stale = self.runtime._set_annotations({
            'path': 'modified.py', 'expected_change_count': change_count + 1,
            'annotations': [{'id': 'intent', 'line': 2, 'markdown': 'Reason'}],
        })
        self.assertFalse(stale.success)
        self.assertIn('stale buffer', stale.text)

        cleared = self.runtime._clear_annotations({'path': 'modified.py'})
        self.assertTrue(cleared.success)
        self.assertEqual(json.loads(cleared.text)['cleared_sets'], 1)

    def test_mdpopups_annotations_receive_the_framed_wrapper(self) -> None:
        responses = []
        self.runtime._open_diff({'base_ref': self.base, 'comparison': 'direct'}, responses.append)
        self.runtime._uses_mdpopups = True
        self.runtime._phantom_cls = MarkdownPhantom

        result = self.runtime._set_annotations({
            'path': 'modified.py',
            'annotations': [{'id': 'intent', 'line': 2, 'markdown': '**Reason**'}],
        })

        self.assertTrue(result.success, result.text)
        phantom = next(iter(self.runtime._annotation_sets.values())).phantoms[0]
        self.assertEqual(phantom.wrapper_class, 'sublime-agent-annotation')
        self.assertIn('border: 1px solid', phantom.css)


if __name__ == '__main__':
    unittest.main()

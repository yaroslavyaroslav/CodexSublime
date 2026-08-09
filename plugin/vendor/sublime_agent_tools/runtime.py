"""Sublime-hosted implementations of the client-defined agent tools."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Callable

import sublime

try:
    from .phantom_markdown import (
        ANNOTATION_CSS,
        ANNOTATION_WRAPPER_CLASS,
        minihtml,
        phantom_classes,
    )
except ImportError:  # Allow the source repository's tests to import this module directly.
    from phantom_markdown import (
        ANNOTATION_CSS,
        ANNOTATION_WRAPPER_CLASS,
        minihtml,
        phantom_classes,
    )


_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


@dataclass(frozen=True)
class ToolResponse:
    success: bool
    text: str

    @classmethod
    def ok(cls, payload: dict[str, Any]) -> 'ToolResponse':
        return cls(True, json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @classmethod
    def error(cls, message: str) -> 'ToolResponse':
        return cls(False, message)


class SublimeToolRuntime:
    """Execute a narrow, workspace-scoped tool allowlist for one Sublime window."""

    def __init__(self, window: Any, cwd: str) -> None:
        self._window = window
        self._cwd = os.path.realpath(cwd)
        folders = window.folders() if window else []
        self._roots = tuple(os.path.realpath(path) for path in (folders or [self._cwd]))
        self._annotation_sets: dict[tuple[int, str], Any] = {}
        self._annotation_specs: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
        self._allowed_lines: dict[str, set[int]] = {}
        self._phantom_cls, self._phantom_set_cls, self._uses_mdpopups = phantom_classes(sublime)

    def execute(self, tool: str, arguments: Any, done: Callable[[ToolResponse], None]) -> None:
        args = arguments if isinstance(arguments, dict) else {}
        if tool == 'open_diff':
            threading.Thread(target=self._open_diff, args=(args, done), daemon=True).start()
        elif tool == 'set_annotations':
            sublime.set_timeout(lambda: done(self._set_annotations(args)), 0)
        elif tool == 'clear_annotations':
            sublime.set_timeout(lambda: done(self._clear_annotations(args)), 0)
        else:
            done(ToolResponse.error(f'Unsupported Sublime tool: {tool}'))

    def _git(self, *args: str) -> str:
        process = subprocess.run(
            ['git', *args], cwd=self._cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='replace', check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or f'git exited {process.returncode}'
            raise RuntimeError(detail)
        return process.stdout

    def _resolve_base(self, base_ref: Any, comparison: Any) -> str:
        if not isinstance(base_ref, str) or not base_ref.strip():
            raise ValueError('base_ref must be a non-empty Git ref')
        verified = self._git('rev-parse', '--verify', '--end-of-options', f'{base_ref}^{{commit}}').strip()
        if comparison == 'direct':
            return verified
        if comparison not in (None, 'merge_base'):
            raise ValueError("comparison must be 'merge_base' or 'direct'")
        return self._git('merge-base', verified, 'HEAD').strip()

    def _scoped_path(self, relative_path: Any) -> tuple[str, str]:
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError('path must be a non-empty workspace-relative path')
        absolute = os.path.realpath(
            relative_path if os.path.isabs(relative_path) else os.path.join(self._cwd, relative_path)
        )
        if not any(absolute == root or absolute.startswith(root + os.sep) for root in self._roots):
            raise ValueError(f'path is outside the bound Sublime workspace: {relative_path}')
        return absolute, os.path.relpath(absolute, self._cwd)

    def _changed_files(self, base: str) -> list[dict[str, str]]:
        fields = self._git('diff', '--name-status', '-z', '-M', base, '--').split('\0')
        result: list[dict[str, str]] = []
        index = 0
        while index < len(fields) and fields[index]:
            status = fields[index]
            index += 1
            kind = status[0]
            if kind in {'R', 'C'}:
                old_path, new_path = fields[index], fields[index + 1]
                index += 2
            else:
                old_path = new_path = fields[index]
                index += 1
            if kind in {'A', 'M', 'T', 'R', 'C'}:
                result.append({'status': kind, 'path': new_path, 'base_path': old_path})
        return result

    def _reference_text(self, base: str, entry: dict[str, str]) -> str:
        if entry['status'] == 'A':
            return ''
        process = subprocess.run(
            ['git', 'show', f"{base}:{entry['base_path']}"], cwd=self._cwd,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(process.stderr.decode('utf-8', errors='replace').strip())
        if b'\0' in process.stdout:
            raise ValueError('binary files cannot be displayed as text diffs')
        return process.stdout.decode('utf-8', errors='replace')

    def _current_changed_lines(self, base: str, path: str) -> set[int]:
        patch = self._git('diff', '--unified=0', '--no-color', base, '--', path)
        lines: set[int] = set()
        for line in patch.splitlines():
            match = _HUNK.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or '1')
                lines.update(range(start, start + count))
        return lines

    def _open_diff(self, args: dict[str, Any], done: Callable[[ToolResponse], None]) -> None:
        try:
            base = self._resolve_base(args.get('base_ref'), args.get('comparison'))
            entries = self._changed_files(base)
            requested = args.get('paths')
            if requested is not None:
                if not isinstance(requested, list) or not all(isinstance(path, str) for path in requested):
                    raise ValueError('paths must be an array of strings')
                wanted = {self._scoped_path(path)[1] for path in requested}
                entries = [entry for entry in entries if entry['path'] in wanted]

            prepared: list[dict[str, Any]] = []
            skipped: list[dict[str, str]] = []
            for entry in entries:
                try:
                    absolute, relative = self._scoped_path(entry['path'])
                    if not os.path.isfile(absolute):
                        skipped.append({'path': relative, 'reason': 'current file is unavailable'})
                        continue
                    with open(absolute, 'rb') as current_file:
                        if b'\0' in current_file.read(8192):
                            skipped.append({'path': relative, 'reason': 'binary file'})
                            continue
                    prepared.append({
                        'absolute': absolute, 'path': relative,
                        'reference': self._reference_text(base, entry),
                        'allowed_lines': self._current_changed_lines(base, relative),
                    })
                except (RuntimeError, ValueError) as exc:
                    skipped.append({'path': entry['path'], 'reason': str(exc)})

            sublime.set_timeout(lambda: self._open_prepared_views(base, prepared, skipped, done), 0)
        except Exception as exc:
            done(ToolResponse.error(f'open_diff failed: {exc}'))

    def _open_prepared_views(
        self, base: str, prepared: list[dict[str, Any]], skipped: list[dict[str, str]],
        done: Callable[[ToolResponse], None],
    ) -> None:
        pending = list(prepared)
        opened: list[dict[str, Any]] = []

        def open_next() -> None:
            if not pending:
                if opened:
                    self._window.focus_view(opened[0]['view'])
                serializable = [{key: value for key, value in item.items() if key != 'view'} for item in opened]
                done(ToolResponse.ok({'base': base, 'opened': serializable, 'skipped': skipped}))
                return
            item = pending.pop(0)
            view = self._window.open_file(item['absolute'])

            def finish(attempts_left: int = 400) -> None:
                if view.is_loading() and attempts_left > 0:
                    sublime.set_timeout(lambda: finish(attempts_left - 1), 25)
                    return
                if view.is_loading():
                    skipped.append({'path': item['path'], 'reason': 'file did not finish loading'})
                    open_next()
                    return
                view.set_reference_document(item['reference'])
                self._allowed_lines[os.path.realpath(item['absolute'])] = item['allowed_lines']
                opened.append({
                    'path': item['path'], 'view_id': view.id(), 'change_count': view.change_count(),
                    'annotatable_lines': sorted(item['allowed_lines']), 'view': view,
                })
                open_next()
            finish()
        open_next()

    def _find_open_view(self, absolute: str) -> Any | None:
        for view in self._window.views():
            file_name = view.file_name()
            if file_name and os.path.realpath(file_name) == absolute:
                return view
        return None

    def _set_annotations(self, args: dict[str, Any]) -> ToolResponse:
        try:
            absolute, relative = self._scoped_path(args.get('path'))
            view = self._find_open_view(absolute)
            if view is None:
                raise ValueError(f'file is not open in the bound Sublime window: {relative}')
            expected = args.get('expected_change_count')
            if expected is not None and expected != view.change_count():
                raise ValueError(f'stale buffer for {relative}: expected {expected}, current {view.change_count()}')
            annotations = args.get('annotations')
            if not isinstance(annotations, list):
                raise ValueError('annotations must be an array')
            group = args.get('group', 'sublime-agent')
            if not isinstance(group, str) or not group:
                raise ValueError('group must be a non-empty string')
            mode = args.get('mode', 'replace')
            if mode not in {'replace', 'upsert'}:
                raise ValueError("mode must be 'replace' or 'upsert'")

            allowed = self._allowed_lines.get(absolute)
            if allowed is None:
                raise ValueError('open_diff must be called for this file before adding annotations')
            key = (view.id(), group)
            specs = {} if mode == 'replace' else dict(self._annotation_specs.get(key, {}))
            for annotation in annotations:
                if not isinstance(annotation, dict):
                    raise ValueError('each annotation must be an object')
                annotation_id = annotation.get('id')
                line = annotation.get('line')
                markdown = annotation.get('markdown')
                column = annotation.get('column', 0)
                if not isinstance(annotation_id, str) or not annotation_id:
                    raise ValueError('annotation id must be a non-empty string')
                if not isinstance(line, int) or line < 1:
                    raise ValueError('annotation line must be a positive integer')
                if line not in allowed:
                    raise ValueError(f'line {line} is not an added or changed line in the active diff')
                if not isinstance(column, int) or column < 0:
                    raise ValueError('annotation column must be a non-negative integer')
                if not isinstance(markdown, str) or not markdown.strip():
                    raise ValueError('annotation markdown must be non-empty')
                specs[annotation_id] = {'line': line, 'column': column, 'markdown': markdown}

            phantoms = []
            for annotation_id in sorted(specs):
                spec = specs[annotation_id]
                point = view.text_point(spec['line'] - 1, spec['column'])
                content = spec['markdown'] if self._uses_mdpopups else minihtml(spec['markdown'])
                phantom_options = (
                    {'css': ANNOTATION_CSS, 'wrapper_class': ANNOTATION_WRAPPER_CLASS}
                    if self._uses_mdpopups
                    else {}
                )
                phantoms.append(self._phantom_cls(
                    sublime.Region(point, point), content, sublime.PhantomLayout.BLOCK,
                    **phantom_options,
                ))
            phantom_set = self._annotation_sets.get(key)
            if phantom_set is None:
                phantom_set = self._phantom_set_cls(view, f'sublime-agent-tools:{group}')
                self._annotation_sets[key] = phantom_set
            phantom_set.update(phantoms)
            self._annotation_specs[key] = specs
            return ToolResponse.ok({
                'path': relative, 'group': group, 'count': len(phantoms),
                'renderer': 'mdpopups' if self._uses_mdpopups else 'minihtml',
            })
        except Exception as exc:
            return ToolResponse.error(f'set_annotations failed: {exc}')

    def _clear_annotations(self, args: dict[str, Any]) -> ToolResponse:
        try:
            path, group = args.get('path'), args.get('group')
            absolute = self._scoped_path(path)[0] if path is not None else None
            cleared = 0
            for key, phantom_set in list(self._annotation_sets.items()):
                view_id, key_group = key
                view = next((candidate for candidate in self._window.views() if candidate.id() == view_id), None)
                if view is None:
                    continue
                file_name = view.file_name()
                if absolute is not None and (not file_name or os.path.realpath(file_name) != absolute):
                    continue
                if group is not None and group != key_group:
                    continue
                phantom_set.update([])
                self._annotation_sets.pop(key, None)
                self._annotation_specs.pop(key, None)
                cleared += 1
            return ToolResponse.ok({'cleared_sets': cleared})
        except Exception as exc:
            return ToolResponse.error(f'clear_annotations failed: {exc}')

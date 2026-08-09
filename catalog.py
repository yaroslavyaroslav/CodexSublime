"""Canonical client-defined tool schemas."""

from __future__ import annotations

from typing import Any


def _function(name: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        'type': 'function',
        'name': name,
        'description': description,
        'inputSchema': input_schema,
    }


def dynamic_tool_namespace() -> dict[str, Any]:
    """Return the app-server dynamic tool namespace for Sublime Text."""

    return {
        'type': 'namespace',
        'name': 'sublime',
        'description': (
            'Present files, Git diffs, annotations, and tabs in the bound Sublime Text window. '
            'Use these tools instead of the Sublime CLI for tab management.'
        ),
        'tools': [
            _function(
                'open_diff',
                (
                    'Open added or changed text files in Sublime and show the inline diff against a Git base. '
                    'Deleted-only files are ignored. Omit paths to open every eligible file.'
                ),
                {
                    'type': 'object',
                    'properties': {
                        'base_ref': {
                            'type': 'string',
                            'description': 'Git branch, tag, or commit used as the comparison base.',
                        },
                        'comparison': {
                            'type': 'string',
                            'enum': ['merge_base', 'direct'],
                            'default': 'merge_base',
                        },
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Optional workspace-relative paths to open.',
                        },
                    },
                    'required': ['base_ref'],
                    'additionalProperties': False,
                },
            ),
            _function(
                'set_annotations',
                (
                    'Atomically add or update Markdown phantoms across one or more open diff files. '
                    'Every file is validated before any annotations are changed. Do not annotate deleted-only lines.'
                ),
                {
                    'type': 'object',
                    'properties': {
                        'group': {'type': 'string', 'default': 'sublime-agent'},
                        'mode': {'type': 'string', 'enum': ['replace', 'upsert'], 'default': 'replace'},
                        'files': {
                            'type': 'array',
                            'minItems': 1,
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'path': {'type': 'string'},
                                    'expected_change_count': {'type': 'integer', 'minimum': 0},
                                    'annotations': {
                                        'type': 'array',
                                        'items': {
                                            'type': 'object',
                                            'properties': {
                                                'id': {'type': 'string'},
                                                'line': {'type': 'integer', 'minimum': 1},
                                                'column': {'type': 'integer', 'minimum': 0, 'default': 0},
                                                'markdown': {'type': 'string'},
                                            },
                                            'required': ['id', 'line', 'markdown'],
                                            'additionalProperties': False,
                                        },
                                    },
                                },
                                'required': ['path', 'annotations'],
                                'additionalProperties': False,
                            },
                        },
                    },
                    'required': ['files'],
                    'additionalProperties': False,
                },
            ),
            _function(
                'clear_annotations',
                'Remove model-created phantoms from one file or the entire bound Sublime window.',
                {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string'},
                        'group': {'type': 'string'},
                    },
                    'additionalProperties': False,
                },
            ),
            _function(
                'list_views',
                (
                    'List every text view open in the bound Sublime window, including its stable view_id, '
                    'absolute path or tab name, group, index, active state, dirty state, and whether this '
                    'runtime opened it. Call this before closing user-selected tabs.'
                ),
                {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
            ),
            _function(
                'close_views',
                (
                    'Close only clean views in the bound Sublime window. For arbitrary user-selected tabs, '
                    'call sublime.list_views first and pass their exact view_ids or paths. Omit both fields '
                    'only to close clean views previously opened by sublime.open_diff. Dirty views are '
                    'always skipped. Never use Sublime CLI bulk-close commands.'
                ),
                {
                    'type': 'object',
                    'properties': {
                        'view_ids': {
                            'type': 'array',
                            'items': {'type': 'integer', 'minimum': 0},
                            'description': 'Exact view IDs returned by sublime.list_views.',
                        },
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': (
                                'Exact absolute paths returned by sublime.list_views or workspace-relative paths.'
                            ),
                        },
                    },
                    'additionalProperties': False,
                },
            ),
        ],
    }

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
        'description': 'Present files, Git diffs, and explanatory annotations in the bound Sublime Text window.',
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
                    'Add or update Markdown phantoms on added or changed current-buffer lines. '
                    'Do not annotate deleted-only lines.'
                ),
                {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string'},
                        'group': {'type': 'string', 'default': 'sublime-agent'},
                        'mode': {'type': 'string', 'enum': ['replace', 'upsert'], 'default': 'replace'},
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
        ],
    }

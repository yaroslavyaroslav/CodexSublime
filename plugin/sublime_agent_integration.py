"""Host adapter for vendored Sublime agent tools and skills."""

from __future__ import annotations

import os
import re

import sublime


SKILL_NAME = 'sublime-explain-diff'
_SKILL_MARKER = f'${SKILL_NAME}'
_EXPLAIN_RE = re.compile(r'\b(explain|describe|walk\s+me\s+through)\b|объясн|опиш|расскаж', re.IGNORECASE)
_DIFF_RE = re.compile(r'\b(diff|branch|changes?|commit)\b|дифф|ветк|изменен|изменён|коммит', re.IGNORECASE)


def should_attach_explain_diff_skill(prompt: str) -> bool:
    """Recognize explicit skill invocation and the narrow natural-language use case."""

    return _SKILL_MARKER in prompt or bool(_EXPLAIN_RE.search(prompt) and _DIFF_RE.search(prompt))


def materialize_explain_diff_skill() -> str:
    """Return a filesystem path even when the plugin is loaded from an archive."""

    relative = os.path.join('vendor', 'sublime_agent_tools', 'skills', SKILL_NAME, 'SKILL.md')
    source_path = os.path.abspath(os.path.join(os.path.dirname(__file__), relative))
    if os.path.isfile(source_path):
        return source_path

    package_name = (__package__ or 'Codex').split('.')[0]
    resource_path = f'Packages/{package_name}/plugin/{relative.replace(os.sep, "/")}'
    contents = sublime.load_resource(resource_path)
    target_dir = os.path.join(sublime.cache_path(), 'Codex', 'skills', SKILL_NAME)
    target_path = os.path.join(target_dir, 'SKILL.md')
    os.makedirs(target_dir, exist_ok=True)
    current = None
    try:
        with open(target_path, encoding='utf-8') as skill_file:
            current = skill_file.read()
    except FileNotFoundError:
        pass
    if current != contents:
        with open(target_path, 'w', encoding='utf-8', newline='\n') as skill_file:
            skill_file.write(contents)
    return target_path


def skill_input_items(prompt: str) -> list[dict[str, str]]:
    if not should_attach_explain_diff_skill(prompt):
        return []
    return [{'type': 'skill', 'name': SKILL_NAME, 'path': materialize_explain_diff_skill()}]

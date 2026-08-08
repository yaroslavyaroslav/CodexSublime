from __future__ import annotations

import os
import sys
import types
import unittest


if 'sublime' not in sys.modules:
    sublime = types.ModuleType('sublime')
    sys.modules['sublime'] = sublime

from plugin.sublime_agent_integration import (
    SKILL_NAME,
    skill_input_items,
    should_attach_explain_diff_skill,
)


class SublimeAgentIntegrationTests(unittest.TestCase):
    def test_detects_explicit_and_natural_language_diff_explanations(self) -> None:
        self.assertTrue(should_attach_explain_diff_skill(f'Use ${SKILL_NAME} here'))
        self.assertTrue(should_attach_explain_diff_skill('Объясни изменения в этой ветке'))
        self.assertTrue(should_attach_explain_diff_skill('Explain this branch diff'))
        self.assertFalse(should_attach_explain_diff_skill('Refactor this function'))

    def test_builds_skill_input_with_real_source_path(self) -> None:
        items = skill_input_items('Опиши изменения в ветке')

        self.assertEqual(items[0]['type'], 'skill')
        self.assertEqual(items[0]['name'], SKILL_NAME)
        self.assertTrue(os.path.isfile(items[0]['path']))


if __name__ == '__main__':
    unittest.main()

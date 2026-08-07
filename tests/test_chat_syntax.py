from __future__ import annotations

import unittest

from plugin.chat_syntax import TRANSCRIPT_VIEW_FLAG, migrate_chat_syntax


TARGET = "Packages/Codex/plugin/vendor/sublime_chat_ui/Syntaxes/ChatMarkdown.sublime-syntax"
LEGACY = "Packages/Codex/Syntaxes/Markdown.sublime-syntax"


class FakeSettings:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str) -> object | None:
        return self.values.get(key)


class FakeView:
    def __init__(self, settings: dict[str, object]) -> None:
        self._settings = FakeSettings(settings)
        self.assigned: list[str] = []

    def settings(self) -> FakeSettings:
        return self._settings

    def assign_syntax(self, path: str) -> None:
        self.assigned.append(path)


class ChatSyntaxMigrationTests(unittest.TestCase):
    def test_migrates_legacy_syntax_path(self) -> None:
        view = FakeView({"syntax": LEGACY})

        self.assertTrue(migrate_chat_syntax(view))
        self.assertEqual([TARGET], view.assigned)

    def test_migrates_transcript_with_missing_syntax(self) -> None:
        view = FakeView({TRANSCRIPT_VIEW_FLAG: True})

        self.assertTrue(migrate_chat_syntax(view))
        self.assertEqual([TARGET], view.assigned)

    def test_ignores_unrelated_view(self) -> None:
        view = FakeView({"syntax": "Packages/Python/Python.sublime-syntax"})

        self.assertFalse(migrate_chat_syntax(view))
        self.assertEqual([], view.assigned)

    def test_does_not_reassign_current_syntax(self) -> None:
        view = FakeView({"syntax": TARGET, TRANSCRIPT_VIEW_FLAG: True})

        self.assertFalse(migrate_chat_syntax(view))
        self.assertEqual([], view.assigned)


if __name__ == "__main__":
    unittest.main()

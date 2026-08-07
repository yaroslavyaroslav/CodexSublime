from __future__ import annotations

import unittest

from plugin.input_history import CodexInputHistoryController


class FakeSettings:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value

    def erase(self, key: str) -> None:
        self.values.pop(key, None)


class FakeWindow:
    def __init__(self, window_id: int, settings: dict[str, object] | None = None) -> None:
        self._id = window_id
        self._settings = FakeSettings(settings)
        self.panel: FakePanel | None = None

    def id(self) -> int:
        return self._id

    def settings(self) -> FakeSettings:
        return self._settings

    def find_output_panel(self, name: str) -> FakePanel | None:
        return self.panel if name == CodexInputHistoryController.PANEL_NAME else None


class FakeRegion:
    def __init__(self, point: int, empty: bool = True) -> None:
        self.point = point
        self._empty = empty

    def empty(self) -> bool:
        return self._empty

    def begin(self) -> int:
        return self.point


class FakePanel:
    def __init__(self, window: FakeWindow, panel_id: int = 10) -> None:
        self._window = window
        self._id = panel_id
        self.selection = [FakeRegion(0)]
        self.positions = {0: (0, 0)}
        window.panel = self

    def id(self) -> int:
        return self._id

    def window(self) -> FakeWindow:
        return self._window

    def sel(self) -> list[FakeRegion]:
        return self.selection

    def rowcol(self, point: int) -> tuple[int, int]:
        return self.positions[point]


class CodexInputHistoryControllerTests(unittest.TestCase):
    def tearDown(self) -> None:
        CodexInputHistoryController._history_sessions.clear()
        CodexInputHistoryController._hiding_windows.clear()

    def test_records_only_valid_existing_history_entries(self) -> None:
        window = FakeWindow(
            1,
            {CodexInputHistoryController.HISTORY_STORAGE_KEY: ['first', '', 3]},
        )

        CodexInputHistoryController.record_history(window, 'second')

        self.assertEqual(['first', 'second'], CodexInputHistoryController.get_history(window))

    def test_draft_round_trip_and_clear(self) -> None:
        window = FakeWindow(1)

        CodexInputHistoryController.save_draft(window, 'unfinished')
        self.assertEqual('unfinished', CodexInputHistoryController.get_draft(window))

        CodexInputHistoryController.clear_draft(window)
        self.assertEqual('', CodexInputHistoryController.get_draft(window))

    def test_navigation_starts_only_at_first_character(self) -> None:
        window = FakeWindow(
            1,
            {CodexInputHistoryController.HISTORY_STORAGE_KEY: ['first']},
        )
        panel = FakePanel(window)

        self.assertTrue(CodexInputHistoryController.should_navigate_previous(panel))

        panel.positions[0] = (0, 1)
        self.assertFalse(CodexInputHistoryController.should_navigate_previous(panel))

    def test_next_navigation_requires_active_history_session(self) -> None:
        window = FakeWindow(
            1,
            {CodexInputHistoryController.HISTORY_STORAGE_KEY: ['first']},
        )
        panel = FakePanel(window)

        self.assertFalse(CodexInputHistoryController.should_navigate_next(panel))
        CodexInputHistoryController.history_session(window).previous(['first'], 'draft')
        self.assertTrue(CodexInputHistoryController.should_navigate_next(panel))

    def test_sessions_are_isolated_by_window(self) -> None:
        first = FakeWindow(1)
        second = FakeWindow(2)

        CodexInputHistoryController.history_session(first).previous(['first'], 'draft')

        self.assertTrue(CodexInputHistoryController.history_session(first).browsing)
        self.assertFalse(CodexInputHistoryController.history_session(second).browsing)

    def test_editing_recalled_history_resets_browsing(self) -> None:
        window = FakeWindow(
            1,
            {CodexInputHistoryController.HISTORY_STORAGE_KEY: ['first']},
        )
        CodexInputHistoryController.history_session(window).previous(['first'], 'draft')

        self.assertFalse(CodexInputHistoryController.reset_if_history_entry_changed(window, 'first'))
        self.assertTrue(CodexInputHistoryController.reset_if_history_entry_changed(window, 'edited'))
        self.assertFalse(CodexInputHistoryController.history_session(window).browsing)

    def test_identifies_only_the_configured_input_panel(self) -> None:
        window = FakeWindow(1)
        panel = FakePanel(window)
        other = FakePanel(window, panel_id=11)
        window.panel = panel

        self.assertTrue(CodexInputHistoryController.is_input_panel_view(panel))
        self.assertFalse(CodexInputHistoryController.is_input_panel_view(other))


if __name__ == '__main__':
    unittest.main()

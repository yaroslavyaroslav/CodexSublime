from __future__ import annotations

import sys
import types
import unittest


if 'sublime' not in sys.modules:
    sublime = types.ModuleType('sublime')
    sublime.Window = object
    sys.modules['sublime'] = sublime

from plugin import bridge_manager


class FakeWindow:
    def __init__(self, window_id: int) -> None:
        self._window_id = window_id

    def id(self) -> int:
        return self._window_id


class FakeBridge:
    def __init__(self, window: FakeWindow | None) -> None:
        self.window = window


class BridgeManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_bridge_class = bridge_manager._CodexBridge
        bridge_manager._CodexBridge = FakeBridge
        bridge_manager.bridges.clear()

    def tearDown(self) -> None:
        bridge_manager._CodexBridge = self.original_bridge_class
        bridge_manager.bridges.clear()

    def test_new_bridge_is_bound_to_requested_window(self) -> None:
        requested_window = FakeWindow(7)

        bridge = bridge_manager.get_bridge(requested_window)

        self.assertIs(requested_window, bridge.window)
        self.assertIs(bridge, bridge_manager.bridges[7])

    def test_existing_bridge_lookup_does_not_create_one(self) -> None:
        requested_window = FakeWindow(7)

        self.assertIsNone(bridge_manager.get_existing_bridge(requested_window))
        self.assertEqual(bridge_manager.bridges, {})

        bridge = bridge_manager.get_bridge(requested_window)

        self.assertIs(bridge_manager.get_existing_bridge(requested_window), bridge)


if __name__ == '__main__':
    unittest.main()

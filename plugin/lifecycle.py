"""Event-listeners & background watchdog for the Codex plugin."""

from __future__ import annotations

import logging

import sublime
import sublime_plugin

from .bridge_manager import bridges

logger = logging.getLogger(__name__)


class CodexWindowEventListener(sublime_plugin.EventListener):
    """Clean up Codex bridges when their associated window is closed."""

    def on_pre_close(self, view):  # type: ignore[override]
        window = view.window()
        if window is None:
            return

        # If this is the last view, the window is about to vanish – pre-empt.
        if len(window.views()) <= 1:
            key = window.id()
            print('[CodexWindowEventListener] on_pre_close triggered for window', key)
            bridge = bridges.pop(key, None)
            if bridge is not None:
                bridge.terminate()


# ---------------------------------------------------------------- watchdog --


def _watchdog_tick():
    live_window_ids = {w.id() for w in sublime.windows()}
    stale_keys = [wid for wid in list(bridges.keys()) if wid not in live_window_ids and wid != '__global__']

    for wid in stale_keys:
        bridge = bridges.pop(wid, None)
        if bridge is not None:
            print('[Codex] watchdog terminating orphaned bridge for window', wid)
            bridge.terminate()

    sublime.set_timeout(_watchdog_tick, 5_000)


# ------------------------------------------------------------- plugin hooks --


def plugin_loaded():  # noqa: D401 – ST hook
    print('[Codex] plugin_loaded – plugin is active')
    _watchdog_tick()


def plugin_unloaded():  # noqa: D401 - ST hook
    print('[Codex] plugin_unloaded – cleaning up bridges')
    for key, bridge in list(bridges.items()):
        bridge.terminate()
        bridges.pop(key, None)

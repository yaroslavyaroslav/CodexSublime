"""Event-listeners & background watchdog for the Codex plugin."""

from __future__ import annotations

import logging

import sublime  # type: ignore
import sublime_plugin  # type: ignore

from . import bridge_manager as bm

logger = logging.getLogger(__name__)


class CodexWindowEventListener(sublime_plugin.EventListener):
    """Clean up Codex bridges when their associated window is closed."""

    def on_pre_close(self, view):  # type: ignore[override]
        window = view.window()

        if window is None:
            # View already detached; we cannot get its window id anymore but we
            # can still sweep for orphaned bridges.
            logger.debug('on_pre_close: view had no window – performing sweep')
            _cleanup_orphan_bridges()
            return

        # If this is the last view, the window is about to vanish – pre-empt.
        if len(window.views()) <= 1:
            key = window.id()
            print('[CodexWindowEventListener] on_pre_close triggered for window', key)
            bridge = bm.bridges.pop(key, None)
            if bridge is not None:
                bridge.terminate()


# ---------------------------------------------------------------- watchdog --


def _watchdog_tick():
    live_window_ids = {w.id() for w in sublime.windows()}
    stale_keys = [
        wid for wid in list(bm.bridges.keys()) if wid not in live_window_ids and wid != '__global__'
    ]

    for wid in stale_keys:
        bridge = bm.bridges.pop(wid, None)
        if bridge is not None:
            print('[Codex] watchdog terminating orphaned bridge for window', wid)
            bridge.terminate()

    sublime.set_timeout(_watchdog_tick, 5_000)


# Allow other callbacks (e.g. on_close) to force an immediate orphan cleanup.


def _cleanup_orphan_bridges():
    live_window_ids = {w.id() for w in sublime.windows()}
    for wid in [wid for wid in list(bm.bridges) if wid not in live_window_ids and wid != '__global__']:
        bridge = bm.bridges.pop(wid, None)
        if bridge is not None:
            logger.info('Immediate cleanup of orphaned bridge for window %s', wid)
            bridge.terminate()


# ------------------------------------------------------------- plugin hooks --


def plugin_loaded():  # noqa: D401 – ST hook
    print('[Codex] plugin_loaded – plugin is active')
    _watchdog_tick()


def plugin_unloaded():  # noqa: D401 - ST hook
    print('[Codex] plugin_unloaded – cleaning up bridges')
    for key, bridge in list(bm.bridges.items()):
        bridge.terminate()
        bm.bridges.pop(key, None)

"""Event-listeners & background watchdog for the Codex plugin."""

import logging

import sublime  # type: ignore
import sublime_plugin  # type: ignore

from . import bridge_manager as bm
from .chat_syntax import migrate_chat_syntax

logger = logging.getLogger(__name__)


class CodexWindowEventListener(sublime_plugin.EventListener):
    """Clean up Codex bridges when their associated window is closed."""

    def on_load(self, view: sublime.View) -> None:  # type: ignore[override]
        migrate_chat_syntax(view)

    def on_activated(self, view: sublime.View) -> None:  # type: ignore[override]
        migrate_chat_syntax(view)

    def on_pre_close(self, view: sublime.View) -> None:  # type: ignore[override]
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
            logger.debug('on_pre_close triggered for window %i', key)
            bridge = bm.bridges.pop(key, None)
            if bridge is not None:
                bridge.terminate()


# ---------------------------------------------------------------- watchdog --


def _watchdog_tick() -> None:
    live_window_ids = {w.id() for w in sublime.windows()}
    stale_keys = [
        wid for wid in list(bm.bridges.keys()) if wid not in live_window_ids and wid != '__global__'
    ]

    for wid in stale_keys:
        bridge = bm.bridges.pop(wid, None)
        if bridge is not None:
            logger.debug('Watchdog terminating orphaned bridge for window %s', wid)
            bridge.terminate()

    sublime.set_timeout(_watchdog_tick, 5_000)


# Allow other callbacks (e.g. on_close) to force an immediate orphan cleanup.


def _cleanup_orphan_bridges() -> None:
    live_window_ids = {w.id() for w in sublime.windows()}
    for wid in [wid for wid in list(bm.bridges) if wid not in live_window_ids and wid != '__global__']:
        bridge = bm.bridges.pop(wid, None)
        if bridge is not None:
            logger.debug('Immediate cleanup of orphaned bridge for window %s', wid)
            bridge.terminate()


# ------------------------------------------------------------- plugin hooks --


def _migrate_open_chat_views() -> None:
    """Migrate restored transcript tabs and the persistent Codex output panel."""

    for window in sublime.windows():
        for view in window.views():
            migrate_chat_syntax(view)

        panel = window.find_output_panel('codex')
        if panel is not None:
            migrate_chat_syntax(panel)


def plugin_loaded() -> None:  # noqa: D401 – ST hook
    logger.debug('plugin_loaded – plugin is active')
    _migrate_open_chat_views()
    # Session restoration may finish after plugin_loaded() on startup.
    sublime.set_timeout(_migrate_open_chat_views, 1_000)
    _watchdog_tick()


def plugin_unloaded() -> None:  # noqa: D401 - ST hook
    logger.debug('plugin_unloaded – cleaning up bridges')
    for key, bridge in list(bm.bridges.items()):
        bridge.terminate()
        bm.bridges.pop(key, None)

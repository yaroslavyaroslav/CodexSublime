"""A per-window registry for Codex bridges.

Sublime Text may have multiple windows open at the same time.  We keep exactly
one *Codex* subprocess ("bridge") for each window so that conversations remain
separate and we don't spawn unnecessary background processes.
"""

from __future__ import annotations

import logging
from typing import Dict

import sublime  # type: ignore

from .codex_bridge import _CodexBridge

logger = logging.getLogger(__name__)
__all__ = ['get_bridge', 'bridges']


# window-id -> bridge
bridges: Dict[str | int, _CodexBridge] = {}


def get_bridge(window: sublime.Window | None):  # type: ignore[name-defined]
    """Return (and lazily create) the bridge bound to *window*."""

    if window is None:
        key: str | int = '__global__'
    else:
        key = window.id()

    if key not in bridges:
        logger.debug('[Codex] creating new bridge for window %i', key)
        bridges[key] = _CodexBridge()
    else:
        logger.debug('[Codex] reusing existing bridge for window %i', key)

    return bridges[key]

"""Codex host adapter for the shared section projection and folding runtime."""

from __future__ import annotations

import logging

import sublime  # type: ignore

from .vendor.sublime_chat_ui.folding import (
    FoldController,
    FoldOverride,
    SectionRuntime,
    section_key,
)
from .vendor.sublime_chat_ui.sections import (
    SectionBlock,
    SectionEdit,
    SectionKey,
    SectionMutation,
)

logger = logging.getLogger(__name__)


def _region(begin: int, end: int):
    return sublime.Region(begin, end)


def _views_for_buffer(view) -> list:
    matches = []
    for window in sublime.windows():
        matches.extend(
            candidate
            for candidate in window.views()
            if candidate.buffer_id() == view.buffer_id()
        )
    return matches or [view]


def _apply_edit(view, edit: SectionEdit) -> None:
    was_read_only = bool(view.is_read_only())
    view.set_read_only(False)
    try:
        view.run_command(
            'codex_apply_transcript_edit',
            {
                'begin': edit.begin,
                'end': edit.end,
                'text': edit.text,
            },
        )
    finally:
        view.set_read_only(was_read_only)


_RUNTIME = SectionRuntime(
    region_factory=_region,
    edit_applier=_apply_edit,
    views_for_buffer=_views_for_buffer,
)


def forget_view(view) -> None:
    try:
        _RUNTIME.forget_view(view)
    except RuntimeError:
        logger.debug('Failed to detach transcript view', exc_info=True)


def invalidate_view(view) -> None:
    try:
        _RUNTIME.invalidate_view(view)
    except RuntimeError:
        logger.debug('Transcript view was invalidated before its session', exc_info=True)


def clear_sessions() -> None:
    _RUNTIME.clear()


def apply_mutation(view, mutation: SectionMutation, fold_names: set[str]) -> SectionBlock:
    return _RUNTIME.apply_mutation(view, mutation, fold_names)


def restore_folds(view, fold_names: set[str]) -> None:
    _RUNTIME.restore(view, fold_names)


def sync_folds(view, fold_names: set[str]) -> None:
    _RUNTIME.sync(view, fold_names)


def reconcile_folds(view) -> None:
    try:
        _RUNTIME.reconcile(view)
    except RuntimeError:
        logger.debug('Failed to reconcile transcript folds', exc_info=True)


def live_item_key(conversation_id: str, item_id: object) -> SectionKey | None:
    return section_key(conversation_id, item_id)


__all__ = [
    'FoldController',
    'FoldOverride',
    'apply_mutation',
    'clear_sessions',
    'forget_view',
    'invalidate_view',
    'live_item_key',
    'reconcile_folds',
    'restore_folds',
    'sync_folds',
]

"""Sublime-facing transcript sessions, edits, and per-view fold policy."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

import sublime  # type: ignore

from .transcript import (
    TranscriptBlock,
    TranscriptDocument,
    TranscriptEdit,
    TranscriptItemKey,
    TranscriptMutation,
)

logger = logging.getLogger(__name__)


class FoldOverride(StrEnum):
    AUTO = 'auto'
    FORCE_OPEN = 'force_open'
    FORCE_FOLDED = 'force_folded'


def _block_identity(block: TranscriptBlock) -> str:
    if block.key is not None:
        return f'{block.key.conversation_id}\0{block.key.item_id}'
    return f'legacy:{block.semantic_digest.hex()}:{block.start}'


def _fold_region(block: TranscriptBlock):
    span = block.fold_span
    if span is None or span.begin >= span.end:
        return None
    return sublime.Region(span.begin, span.end)


@dataclass(slots=True)
class FoldController:
    """Keep policy-derived folds separate for every clone view."""

    overrides: dict[str, FoldOverride] = field(default_factory=dict)
    expected: dict[str, bool] = field(default_factory=dict)
    suppress_reconcile: bool = False

    def reconcile(self, view, blocks: list[TranscriptBlock]) -> None:
        if self.suppress_reconcile:
            return
        for block in blocks:
            identity = _block_identity(block)
            if identity not in self.expected:
                continue
            region = _fold_region(block)
            if region is None:
                continue
            actual = bool(view.is_folded(region))
            if actual == self.expected[identity]:
                continue
            self.overrides[identity] = FoldOverride.FORCE_FOLDED if actual else FoldOverride.FORCE_OPEN
            self.expected[identity] = actual

    def apply(
        self,
        view,
        blocks: list[TranscriptBlock],
        fold_names: set[str],
        *,
        reconcile_manual: bool = True,
    ) -> None:
        if reconcile_manual:
            self.reconcile(view, blocks)
        self.suppress_reconcile = True
        try:
            for block in blocks:
                region = _fold_region(block)
                if region is None:
                    continue
                identity = _block_identity(block)
                override = self.overrides.get(identity, FoldOverride.AUTO)
                policy_folded = block.title.strip().lower() in fold_names
                should_fold = override is FoldOverride.FORCE_FOLDED or (
                    override is FoldOverride.AUTO and policy_folded
                )
                actual = bool(view.is_folded(region))
                if actual and not should_fold:
                    view.unfold(region)
                elif should_fold and not actual:
                    view.fold(region)
                self.expected[identity] = should_fold
        finally:
            self.suppress_reconcile = False

    def transfer(self, previous: TranscriptBlock, current: TranscriptBlock) -> None:
        previous_id = _block_identity(previous)
        current_id = _block_identity(current)
        if previous_id == current_id:
            return
        if previous_id in self.overrides:
            self.overrides[current_id] = self.overrides.pop(previous_id)
        if previous_id in self.expected:
            self.expected[current_id] = self.expected.pop(previous_id)


@dataclass(slots=True)
class TranscriptSession:
    buffer_id: int
    document: TranscriptDocument
    bindings: dict[int, FoldController] = field(default_factory=dict)

    def controller(self, view) -> FoldController:
        return self.bindings.setdefault(view.id(), FoldController())


_SESSIONS: dict[int, TranscriptSession] = {}


def _view_text(view) -> str:
    return view.substr(sublime.Region(0, view.size()))


def session_for(view) -> TranscriptSession:
    buffer_id = view.buffer_id()
    session = _SESSIONS.get(buffer_id)
    if session is None or session.document.expected_size != view.size():
        bindings = session.bindings if session is not None else {}
        session = TranscriptSession(
            buffer_id,
            TranscriptDocument(_view_text(view)),
            bindings=bindings,
        )
        _SESSIONS[buffer_id] = session
    session.controller(view)
    return session


def forget_view(view) -> None:
    try:
        session = _SESSIONS.get(view.buffer_id())
        if session is None:
            return
        session.bindings.pop(view.id(), None)
        if not session.bindings:
            _SESSIONS.pop(session.buffer_id, None)
    except Exception:
        logger.debug('Failed to detach transcript view', exc_info=True)


def invalidate_view(view) -> None:
    try:
        buffer_id = view.buffer_id()
    except RuntimeError:
        logger.debug('Transcript view was invalidated before its session', exc_info=True)
        return
    _SESSIONS.pop(buffer_id, None)


def clear_sessions() -> None:
    _SESSIONS.clear()


def _apply_edit(view, edit: TranscriptEdit) -> None:
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


def apply_mutation(view, mutation: TranscriptMutation, fold_names: set[str]) -> TranscriptBlock:
    session = session_for(view)
    document = session.document
    buffer_ends_with_newline = view.size() == 0 or view.substr(view.size() - 1) == '\n'
    edit = document.reduce(mutation, buffer_ends_with_newline=buffer_ends_with_newline)
    if edit is None:
        existing = document.by_key.get(mutation.key)
        if existing is None:
            raise RuntimeError('Transcript mutation produced neither edit nor block')
        return existing

    bound_views = _views_for_buffer(view)
    for bound_view in bound_views:
        session.controller(bound_view)
    controllers = list(session.bindings.values())
    if edit.previous is not None:
        old_region = _fold_region(edit.previous)
        for bound_view in bound_views:
            controller = session.controller(bound_view)
            controller.reconcile(bound_view, [edit.previous])
    else:
        old_region = None

    for controller in controllers:
        controller.suppress_reconcile = True
    try:
        if old_region is not None:
            for bound_view in bound_views:
                if bound_view.is_folded(old_region):
                    bound_view.unfold(old_region)
        _apply_edit(view, edit)
    except Exception:
        invalidate_view(view)
        raise
    finally:
        for controller in controllers:
            controller.suppress_reconcile = False

    for controller in controllers:
        if edit.previous is not None:
            controller.transfer(edit.previous, edit.block)
    for bound_view in bound_views:
        session.controller(bound_view).apply(
            bound_view,
            [edit.block],
            fold_names,
            reconcile_manual=False,
        )
    return edit.block


def _views_for_buffer(view) -> list:
    matches = []
    for window in sublime.windows():
        matches.extend(candidate for candidate in window.views() if candidate.buffer_id() == view.buffer_id())
    return matches or [view]


def restore_folds(view, fold_names: set[str]) -> None:
    session = session_for(view)
    session.controller(view).apply(view, session.document.blocks, fold_names)


def sync_folds(view, fold_names: set[str]) -> None:
    restore_folds(view, fold_names)


def reconcile_folds(view) -> None:
    try:
        session = session_for(view)
        session.controller(view).reconcile(view, session.document.blocks)
    except Exception:
        logger.debug('Failed to reconcile transcript folds', exc_info=True)


def live_item_key(conversation_id: str, item_id: object) -> TranscriptItemKey | None:
    if item_id is None:
        return None
    item_text = str(item_id).strip()
    if not item_text:
        return None
    return TranscriptItemKey(conversation_id=conversation_id, item_id=item_text)

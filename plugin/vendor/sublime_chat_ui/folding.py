"""Host-neutral per-buffer section sessions and per-view folding policy."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

try:
    from .sections import (
        DEFAULT_SECTION_FORMAT,
        SectionBlock,
        SectionDocument,
        SectionEdit,
        SectionFormat,
        SectionKey,
        SectionMutation,
        SectionStatus,
    )
except ImportError:  # pragma: no cover - direct source-repo test imports
    from sections import (
        DEFAULT_SECTION_FORMAT,
        SectionBlock,
        SectionDocument,
        SectionEdit,
        SectionFormat,
        SectionKey,
        SectionMutation,
        SectionStatus,
    )


RegionFactory = Callable[[int, int], Any]
EditApplier = Callable[[Any, SectionEdit], None]
ViewSupplier = Callable[[Any], Iterable[Any]]


class FoldOverride(StrEnum):
    AUTO = "auto"
    FORCE_OPEN = "force_open"
    FORCE_FOLDED = "force_folded"


def _block_identity(block: SectionBlock) -> str:
    if block.key is not None:
        return f"{block.key.namespace}\0{block.key.item_id}"
    return f"legacy:{block.semantic_digest.hex()}:{block.start}"


def _fold_region(block: SectionBlock, region_factory: RegionFactory):
    span = block.fold_span
    if span is None or span.begin >= span.end:
        return None
    return region_factory(span.begin, span.end)


@dataclass(slots=True)
class FoldController:
    """Keep policy-derived folds and manual overrides separate for every clone."""

    overrides: dict[str, FoldOverride] = field(default_factory=dict)
    expected: dict[str, bool] = field(default_factory=dict)
    suppress_reconcile: bool = False

    def reconcile(
        self,
        view: Any,
        blocks: list[SectionBlock],
        region_factory: RegionFactory,
    ) -> None:
        if self.suppress_reconcile:
            return
        for block in blocks:
            identity = _block_identity(block)
            if identity not in self.expected:
                continue
            region = _fold_region(block, region_factory)
            if region is None:
                continue
            actual = bool(view.is_folded(region))
            if actual == self.expected[identity]:
                continue
            self.overrides[identity] = (
                FoldOverride.FORCE_FOLDED if actual else FoldOverride.FORCE_OPEN
            )
            self.expected[identity] = actual

    def apply(
        self,
        view: Any,
        blocks: list[SectionBlock],
        fold_names: set[str],
        region_factory: RegionFactory,
        *,
        fold_active: bool = True,
        reconcile_manual: bool = True,
    ) -> None:
        if reconcile_manual:
            self.reconcile(view, blocks, region_factory)
        self.suppress_reconcile = True
        try:
            for block in blocks:
                region = _fold_region(block, region_factory)
                if region is None:
                    continue
                identity = _block_identity(block)
                override = self.overrides.get(identity, FoldOverride.AUTO)
                policy_folded = block.title.strip().lower() in fold_names and (
                    fold_active or block.status is SectionStatus.TERMINAL
                )
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

    def transfer(self, previous: SectionBlock, current: SectionBlock) -> None:
        previous_id = _block_identity(previous)
        current_id = _block_identity(current)
        if previous_id == current_id:
            return
        if previous_id in self.overrides:
            self.overrides[current_id] = self.overrides.pop(previous_id)
        if previous_id in self.expected:
            self.expected[current_id] = self.expected.pop(previous_id)


@dataclass(slots=True)
class SectionSession:
    buffer_id: int
    document: SectionDocument
    bindings: dict[int, FoldController] = field(default_factory=dict)

    def controller(self, view: Any) -> FoldController:
        return self.bindings.setdefault(view.id(), FoldController())


class SectionRuntime:
    """Coordinate shared section state while leaving Sublime commands in the host."""

    def __init__(
        self,
        *,
        region_factory: RegionFactory,
        edit_applier: EditApplier,
        views_for_buffer: ViewSupplier,
        section_format: SectionFormat = DEFAULT_SECTION_FORMAT,
    ) -> None:
        self.region_factory = region_factory
        self.edit_applier = edit_applier
        self.views_for_buffer = views_for_buffer
        self.section_format = section_format
        self.sessions: dict[int, SectionSession] = {}

    def _view_text(self, view: Any) -> str:
        return view.substr(self.region_factory(0, view.size()))

    def session_for(self, view: Any) -> SectionSession:
        buffer_id = view.buffer_id()
        session = self.sessions.get(buffer_id)
        if session is None or session.document.expected_size != view.size():
            bindings = session.bindings if session is not None else {}
            session = SectionSession(
                buffer_id,
                SectionDocument(
                    self._view_text(view),
                    section_format=self.section_format,
                ),
                bindings=bindings,
            )
            self.sessions[buffer_id] = session
        session.controller(view)
        return session

    def forget_view(self, view: Any) -> None:
        session = self.sessions.get(view.buffer_id())
        if session is None:
            return
        session.bindings.pop(view.id(), None)
        if not session.bindings:
            self.sessions.pop(session.buffer_id, None)

    def invalidate_view(self, view: Any) -> None:
        self.sessions.pop(view.buffer_id(), None)

    def clear(self) -> None:
        self.sessions.clear()

    def apply_mutation(
        self,
        view: Any,
        mutation: SectionMutation,
        fold_names: set[str],
        *,
        fold_active: bool = True,
    ) -> SectionBlock:
        session = self.session_for(view)
        document = session.document
        previous = document.by_key.get(mutation.key)
        affected_index = (
            document.blocks.index(previous)
            if previous is not None
            else len(document.blocks)
        )
        old_regions = [
            region
            for block in document.blocks[affected_index:]
            if (region := _fold_region(block, self.region_factory)) is not None
        ]
        buffer_ends_with_newline = (
            view.size() == 0 or view.substr(view.size() - 1) == "\n"
        )
        edit = document.reduce(
            mutation, buffer_ends_with_newline=buffer_ends_with_newline
        )
        if edit is None:
            existing = document.by_key.get(mutation.key)
            if existing is None:
                raise RuntimeError("Section mutation produced neither edit nor block")
            return existing

        bound_views = list(self.views_for_buffer(view)) or [view]
        for bound_view in bound_views:
            session.controller(bound_view)
        controllers = list(session.bindings.values())
        affected_blocks = document.blocks[affected_index:]

        for controller in controllers:
            controller.suppress_reconcile = True
        try:
            for bound_view in bound_views:
                for old_region in old_regions:
                    # Sublime may normalize or partially shift folds when an
                    # earlier live section grows. Sampling that geometry here
                    # can look like a manual unfold and persist a false
                    # FORCE_OPEN override. Host events reconcile real user
                    # commands; structural edits clear every shifted fold
                    # before applying the edit and exact current geometry.
                    bound_view.unfold(old_region)
            self.edit_applier(view, edit)
        except Exception:
            self.invalidate_view(view)
            raise
        finally:
            for controller in controllers:
                controller.suppress_reconcile = False

        for controller in controllers:
            if edit.previous is not None:
                controller.transfer(edit.previous, edit.block)
        new_regions = (
            [
                region
                for block in affected_blocks
                if (region := _fold_region(block, self.region_factory)) is not None
            ]
            if edit.previous is not None
            else []
        )
        for bound_view in bound_views:
            for new_region in new_regions:
                # A buffer edit can leave Sublime with a normalized fragment
                # after the old regions were unfolded. Clear post-edit
                # geometry before applying policy; FORCE_OPEN remains open,
                # while AUTO and FORCE_FOLDED recreate every complete span.
                bound_view.unfold(new_region)
            session.controller(bound_view).apply(
                bound_view,
                affected_blocks,
                fold_names,
                self.region_factory,
                fold_active=fold_active,
                reconcile_manual=False,
            )
        return edit.block

    def restore(self, view: Any, fold_names: set[str]) -> None:
        session = self.session_for(view)
        session.controller(view).apply(
            view,
            session.document.blocks,
            fold_names,
            self.region_factory,
        )

    def sync(self, view: Any, fold_names: set[str]) -> None:
        self.restore(view, fold_names)

    def reconcile(self, view: Any) -> None:
        session = self.session_for(view)
        session.controller(view).reconcile(
            view,
            session.document.blocks,
            self.region_factory,
        )


def section_key(namespace: str, item_id: object) -> SectionKey | None:
    if item_id is None:
        return None
    item_text = str(item_id).strip()
    if not item_text:
        return None
    return SectionKey(namespace=namespace, item_id=item_text)

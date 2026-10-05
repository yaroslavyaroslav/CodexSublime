"""Typed, host-neutral Markdown section model and exact text geometry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import blake2s

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionFormat:
    """The textual contract used to serialize and recover chat sections."""

    separator: str = "----------"
    heading_levels: frozenset[int] = frozenset({2, 3})

    def __post_init__(self) -> None:
        if not self.separator or "\n" in self.separator or "\r" in self.separator:
            raise ValueError("Section separator must be one non-empty line")
        if not self.heading_levels or not self.heading_levels <= frozenset(range(1, 7)):
            raise ValueError("Section heading levels must be between 1 and 6")

    @property
    def separator_text(self) -> str:
        return f"{self.separator}\n\n"


DEFAULT_SECTION_FORMAT = SectionFormat()


class SectionMutationKind(StrEnum):
    CREATE = "create"
    APPEND_DELTA = "append_delta"
    UPSERT = "upsert"
    FINALIZE = "finalize"


class SectionStatus(StrEnum):
    ACTIVE = "active"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionKey:
    namespace: str
    item_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionMutation:
    kind: SectionMutationKind
    key: SectionKey
    header: str | None
    body: str


@dataclass(frozen=True, slots=True)
class TextSpan:
    begin: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.begin


@dataclass(frozen=True, slots=True)
class SectionLayout:
    whole: TextSpan
    separator: TextSpan
    heading: TextSpan
    body: TextSpan
    fold: TextSpan | None
    guard: TextSpan


@dataclass(frozen=True, slots=True)
class SerializedSection:
    text: str
    layout: SectionLayout
    title: str


@dataclass(slots=True)
class SectionBlock:
    start: int
    text: str
    layout: SectionLayout
    title: str
    key: SectionKey | None = None
    header: str | None = None
    body: str | None = None
    status: SectionStatus = SectionStatus.TERMINAL
    semantic_digest: bytes = field(default=b"", repr=False)

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    def absolute(self, span: TextSpan) -> TextSpan:
        return TextSpan(self.start + span.begin, self.start + span.end)

    @property
    def fold_span(self) -> TextSpan | None:
        if self.layout.fold is None:
            return None
        return self.absolute(self.layout.fold)


@dataclass(frozen=True, slots=True)
class SectionEdit:
    begin: int
    end: int
    text: str
    block: SectionBlock
    previous: SectionBlock | None = None


def _heading_match(line: str, section_format: SectionFormat):
    match = _HEADING_RE.match(line)
    if match is None or len(match.group(1)) not in section_format.heading_levels:
        return None
    return match


def _normalize_header(header: str, section_format: SectionFormat) -> str:
    heading = header.splitlines()[0].rstrip()
    if _heading_match(heading, section_format) is None:
        levels = ", ".join(
            str(level) for level in sorted(section_format.heading_levels)
        )
        raise ValueError(
            f"Section header must use configured heading level ({levels}): {heading!r}"
        )
    return f"{heading}\n\n"


def _normalize_body(body: str) -> str:
    """Return body text with exactly one structural guard newline at the end."""

    return body.rstrip("\n") + "\n\n" if body else "\n"


def serialize_section(
    header: str,
    body: str,
    *,
    leading_newline: bool = False,
    section_format: SectionFormat = DEFAULT_SECTION_FORMAT,
) -> SerializedSection:
    """Serialize one section and return exact local spans used by folding."""

    normalized_header = _normalize_header(header, section_format)
    normalized_body = _normalize_body(body)
    prefix = ("\n" if leading_newline else "") + section_format.separator_text
    text = prefix + normalized_header + normalized_body

    separator_begin = 1 if leading_newline else 0
    separator_end = len(prefix)
    heading_begin = separator_end
    heading_line_end = text.index("\n", heading_begin)
    body_begin = heading_line_end
    guard_begin = len(text) - 1
    fold = TextSpan(body_begin, guard_begin) if body_begin < guard_begin else None
    title = text[heading_begin:heading_line_end].lstrip("#").strip()

    layout = SectionLayout(
        whole=TextSpan(0, len(text)),
        separator=TextSpan(separator_begin, separator_end),
        heading=TextSpan(heading_begin, heading_line_end),
        body=TextSpan(body_begin, guard_begin),
        fold=fold,
        guard=TextSpan(guard_begin, len(text)),
    )
    assert layout.fold is None or layout.fold.end == layout.guard.begin
    assert text[layout.guard.begin : layout.guard.end] == "\n"
    assert (
        layout.fold is None
        or layout.fold.end <= layout.separator.begin
        or (layout.fold.begin >= layout.separator.end)
    )
    return SerializedSection(text=text, layout=layout, title=title)


def _digest(text: str) -> bytes:
    return blake2s(text.encode("utf-8"), digest_size=16, person=b"chatsect").digest()


def _fence_transition(
    line: str, active: tuple[str, int] | None
) -> tuple[str, int] | None:
    match = _FENCE_RE.match(line)
    if match is None:
        return active
    marker = match.group(1)
    candidate = (marker[0], len(marker))
    if active is None:
        return candidate
    if candidate[0] == active[0] and candidate[1] >= active[1]:
        return None
    return active


def parse_sections(
    text: str,
    section_format: SectionFormat = DEFAULT_SECTION_FORMAT,
) -> list[SectionBlock]:
    """Recover section geometry without relying on syntax scopes."""

    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line)

    separator_starts: list[int] = []
    active_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        if active_fence is None and bare == section_format.separator:
            probe = index + 1
            while probe < len(lines) and not lines[probe].strip():
                probe += 1
            if probe < len(lines) and _heading_match(
                lines[probe].rstrip("\r\n"), section_format
            ):
                separator_starts.append(line_starts[index])
                continue
        active_fence = _fence_transition(bare, active_fence)

    blocks: list[SectionBlock] = []
    for ordinal, start in enumerate(separator_starts):
        end = (
            separator_starts[ordinal + 1]
            if ordinal + 1 < len(separator_starts)
            else len(text)
        )
        separator_line_end = text.find("\n", start, end)
        if separator_line_end < 0:
            continue
        heading_begin = separator_line_end + 1
        while heading_begin < end and text[heading_begin] in "\r\n":
            heading_begin += 1
        heading_line_end = text.find("\n", heading_begin, end)
        if heading_line_end < 0:
            heading_line_end = end
        heading_match = _heading_match(
            text[heading_begin:heading_line_end].rstrip("\r"),
            section_format,
        )
        if heading_match is None:
            continue

        guard_begin = (
            end - 1 if end > heading_line_end and text[end - 1] == "\n" else end
        )
        fold = TextSpan(heading_line_end - start, guard_begin - start)
        if fold.begin >= fold.end:
            fold = None
        local_separator_end = heading_begin - start
        block_text = text[start:end]
        layout = SectionLayout(
            whole=TextSpan(0, len(block_text)),
            separator=TextSpan(0, local_separator_end),
            heading=TextSpan(heading_begin - start, heading_line_end - start),
            body=TextSpan(heading_line_end - start, guard_begin - start),
            fold=fold,
            guard=TextSpan(guard_begin - start, end - start),
        )
        blocks.append(
            SectionBlock(
                start=start,
                text=block_text,
                layout=layout,
                title=heading_match.group(2).strip(),
                semantic_digest=_digest(block_text),
            )
        )
    return blocks


def _minimal_edit(
    previous: SectionBlock, current: SectionBlock
) -> tuple[int, int, str]:
    old = previous.text
    new = current.text
    prefix = 0
    prefix_limit = min(len(old), len(new))
    while prefix < prefix_limit and old[prefix] == new[prefix]:
        prefix += 1

    suffix = 0
    suffix_limit = min(len(old) - prefix, len(new) - prefix)
    while (
        suffix < suffix_limit
        and old[len(old) - suffix - 1] == new[len(new) - suffix - 1]
    ):
        suffix += 1

    old_end = len(old) - suffix
    new_end = len(new) - suffix
    return previous.start + prefix, previous.start + old_end, new[prefix:new_end]


class SectionDocument:
    """Per-buffer section index and stable live-section identity map."""

    def __init__(
        self,
        text: str = "",
        *,
        section_format: SectionFormat = DEFAULT_SECTION_FORMAT,
    ) -> None:
        self.section_format = section_format
        self.blocks = parse_sections(text, section_format)
        self.by_key: dict[SectionKey, SectionBlock] = {}
        self.expected_size = len(text)

    def append(
        self, mutation: SectionMutation, *, buffer_ends_with_newline: bool
    ) -> SectionEdit:
        leading_newline = self.expected_size > 0 and not buffer_ends_with_newline
        rendered = serialize_section(
            mutation.header or "### unknown",
            mutation.body,
            leading_newline=leading_newline,
            section_format=self.section_format,
        )
        block = SectionBlock(
            start=self.expected_size,
            text=rendered.text,
            layout=rendered.layout,
            title=rendered.title,
            key=mutation.key,
            header=mutation.header,
            body=mutation.body,
            status=(
                SectionStatus.TERMINAL
                if mutation.kind is SectionMutationKind.FINALIZE
                else SectionStatus.ACTIVE
            ),
            semantic_digest=_digest(rendered.text),
        )
        self.blocks.append(block)
        self.by_key[mutation.key] = block
        self.expected_size += len(rendered.text)
        return SectionEdit(block.start, block.start, rendered.text, block)

    def reduce(
        self,
        mutation: SectionMutation,
        *,
        buffer_ends_with_newline: bool,
    ) -> SectionEdit | None:
        previous = self.by_key.get(mutation.key)
        if previous is None:
            return self.append(
                mutation, buffer_ends_with_newline=buffer_ends_with_newline
            )

        if previous.status is SectionStatus.TERMINAL:
            return None

        header = mutation.header or previous.header or f"### {previous.title}"
        if mutation.kind in {
            SectionMutationKind.APPEND_DELTA,
            SectionMutationKind.FINALIZE,
        }:
            body = (previous.body or "") + mutation.body
        else:
            body = mutation.body

        leading_newline = previous.text.startswith("\n")
        rendered = serialize_section(
            header,
            body,
            leading_newline=leading_newline,
            section_format=self.section_format,
        )
        block = SectionBlock(
            start=previous.start,
            text=rendered.text,
            layout=rendered.layout,
            title=rendered.title,
            key=mutation.key,
            header=header,
            body=body,
            status=(
                SectionStatus.TERMINAL
                if mutation.kind is SectionMutationKind.FINALIZE
                else SectionStatus.ACTIVE
            ),
            semantic_digest=_digest(rendered.text),
        )
        index = self.blocks.index(previous)
        if block.semantic_digest == previous.semantic_digest:
            self.blocks[index] = block
            self.by_key[mutation.key] = block
            return SectionEdit(previous.end, previous.end, "", block, previous)

        begin, end, replacement = _minimal_edit(previous, block)
        delta = len(block.text) - len(previous.text)
        self.blocks[index] = block
        self.by_key[mutation.key] = block
        for following in self.blocks[index + 1 :]:
            following.start += delta
        self.expected_size += delta
        return SectionEdit(begin, end, replacement, block, previous)

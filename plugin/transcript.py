"""Typed transcript model and syntax-independent Markdown block geometry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import blake2s

TRANSCRIPT_SEPARATOR = '----------\n\n'
_HEADING_RE = re.compile(r'^(#{2,3})[ \t]+(.+?)[ \t]*$')
_FENCE_RE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})')


class TranscriptItemKind(StrEnum):
    COMMAND_EXECUTION = 'command_execution'
    FILE_CHANGE = 'file_change'
    MCP_TOOL_CALL = 'mcp_tool_call'
    MESSAGE = 'message'
    OTHER = 'other'


class TranscriptMutationKind(StrEnum):
    CREATE = 'create'
    APPEND_DELTA = 'append_delta'
    UPSERT = 'upsert'
    FINALIZE = 'finalize'


class TranscriptItemStatus(StrEnum):
    ACTIVE = 'active'
    TERMINAL = 'terminal'


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptItemKey:
    conversation_id: str
    item_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptMutation:
    kind: TranscriptMutationKind
    key: TranscriptItemKey
    item_kind: TranscriptItemKind
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
class BlockLayout:
    whole: TextSpan
    separator: TextSpan
    heading: TextSpan
    body: TextSpan
    fold: TextSpan | None
    guard: TextSpan


@dataclass(frozen=True, slots=True)
class SerializedBlock:
    text: str
    layout: BlockLayout
    title: str


@dataclass(slots=True)
class TranscriptBlock:
    start: int
    text: str
    layout: BlockLayout
    title: str
    key: TranscriptItemKey | None = None
    item_kind: TranscriptItemKind = TranscriptItemKind.OTHER
    header: str | None = None
    body: str | None = None
    status: TranscriptItemStatus = TranscriptItemStatus.TERMINAL
    semantic_digest: bytes = field(default=b'', repr=False)

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
class TranscriptEdit:
    begin: int
    end: int
    text: str
    block: TranscriptBlock
    previous: TranscriptBlock | None = None


def _normalize_header(header: str) -> str:
    heading = header.splitlines()[0].rstrip()
    if not _HEADING_RE.match(heading):
        raise ValueError(f'Transcript header is not a level 2/3 heading: {heading!r}')
    return f'{heading}\n\n'


def _normalize_body(body: str) -> str:
    """Return body text with exactly one structural guard newline at the end."""

    return body.rstrip('\n') + '\n\n' if body else '\n'


def serialize_block(header: str, body: str, *, leading_newline: bool = False) -> SerializedBlock:
    """Serialize one block and return exact local spans used by folding."""

    normalized_header = _normalize_header(header)
    normalized_body = _normalize_body(body)
    prefix = ('\n' if leading_newline else '') + TRANSCRIPT_SEPARATOR
    text = prefix + normalized_header + normalized_body

    separator_begin = 1 if leading_newline else 0
    separator_end = len(prefix)
    heading_begin = separator_end
    heading_line_end = text.index('\n', heading_begin)
    body_begin = heading_line_end
    guard_begin = len(text) - 1
    fold = TextSpan(body_begin, guard_begin) if body_begin < guard_begin else None
    title = text[heading_begin:heading_line_end].lstrip('#').strip()

    layout = BlockLayout(
        whole=TextSpan(0, len(text)),
        separator=TextSpan(separator_begin, separator_end),
        heading=TextSpan(heading_begin, heading_line_end),
        body=TextSpan(body_begin, guard_begin),
        fold=fold,
        guard=TextSpan(guard_begin, len(text)),
    )
    assert layout.fold is None or layout.fold.end == layout.guard.begin
    assert text[layout.guard.begin : layout.guard.end] == '\n'
    assert (
        layout.fold is None
        or layout.fold.end <= layout.separator.begin
        or (layout.fold.begin >= layout.separator.end)
    )
    return SerializedBlock(text=text, layout=layout, title=title)


def _digest(text: str) -> bytes:
    return blake2s(text.encode('utf-8'), digest_size=16, person=b'cdxtrnsc').digest()


def _fence_transition(line: str, active: tuple[str, int] | None) -> tuple[str, int] | None:
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


def parse_blocks(text: str) -> list[TranscriptBlock]:
    """Parse transcript blocks without relying on Markdown syntax scopes."""

    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line)

    separator_starts: list[int] = []
    active_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        bare = line.rstrip('\r\n')
        if active_fence is None and bare == '----------':
            probe = index + 1
            while probe < len(lines) and not lines[probe].strip():
                probe += 1
            if probe < len(lines) and _HEADING_RE.match(lines[probe].rstrip('\r\n')):
                separator_starts.append(line_starts[index])
                continue
        active_fence = _fence_transition(bare, active_fence)

    blocks: list[TranscriptBlock] = []
    for ordinal, start in enumerate(separator_starts):
        end = separator_starts[ordinal + 1] if ordinal + 1 < len(separator_starts) else len(text)
        separator_line_end = text.find('\n', start, end)
        if separator_line_end < 0:
            continue
        heading_begin = separator_line_end + 1
        while heading_begin < end and text[heading_begin] in '\r\n':
            heading_begin += 1
        heading_line_end = text.find('\n', heading_begin, end)
        if heading_line_end < 0:
            heading_line_end = end
        heading_match = _HEADING_RE.match(text[heading_begin:heading_line_end].rstrip('\r'))
        if heading_match is None:
            continue

        guard_begin = end - 1 if end > heading_line_end and text[end - 1] == '\n' else end
        fold = TextSpan(heading_line_end - start, guard_begin - start)
        if fold.begin >= fold.end:
            fold = None
        local_separator_end = heading_begin - start
        block_text = text[start:end]
        layout = BlockLayout(
            whole=TextSpan(0, len(block_text)),
            separator=TextSpan(0, local_separator_end),
            heading=TextSpan(heading_begin - start, heading_line_end - start),
            body=TextSpan(heading_line_end - start, guard_begin - start),
            fold=fold,
            guard=TextSpan(guard_begin - start, end - start),
        )
        blocks.append(
            TranscriptBlock(
                start=start,
                text=block_text,
                layout=layout,
                title=heading_match.group(2).strip(),
                semantic_digest=_digest(block_text),
            )
        )
    return blocks


class TranscriptDocument:
    """Per-buffer block index and stable live-item identity map."""

    def __init__(self, text: str = '') -> None:
        self.blocks = parse_blocks(text)
        self.by_key: dict[TranscriptItemKey, TranscriptBlock] = {}
        self.expected_size = len(text)

    def append(self, mutation: TranscriptMutation, *, buffer_ends_with_newline: bool) -> TranscriptEdit:
        leading_newline = self.expected_size > 0 and not buffer_ends_with_newline
        rendered = serialize_block(
            mutation.header or '### unknown', mutation.body, leading_newline=leading_newline
        )
        block = TranscriptBlock(
            start=self.expected_size,
            text=rendered.text,
            layout=rendered.layout,
            title=rendered.title,
            key=mutation.key,
            item_kind=mutation.item_kind,
            header=mutation.header,
            body=mutation.body,
            status=(
                TranscriptItemStatus.TERMINAL
                if mutation.kind is TranscriptMutationKind.FINALIZE
                else TranscriptItemStatus.ACTIVE
            ),
            semantic_digest=_digest(rendered.text),
        )
        self.blocks.append(block)
        self.by_key[mutation.key] = block
        self.expected_size += len(rendered.text)
        return TranscriptEdit(block.start, block.start, rendered.text, block)

    def reduce(
        self, mutation: TranscriptMutation, *, buffer_ends_with_newline: bool
    ) -> TranscriptEdit | None:
        previous = self.by_key.get(mutation.key)
        if previous is None:
            return self.append(mutation, buffer_ends_with_newline=buffer_ends_with_newline)

        if previous.status is TranscriptItemStatus.TERMINAL:
            return None

        header = mutation.header or previous.header or f'### {previous.title}'
        if mutation.kind in {
            TranscriptMutationKind.APPEND_DELTA,
            TranscriptMutationKind.FINALIZE,
        }:
            body = (previous.body or '') + mutation.body
        else:
            body = mutation.body

        leading_newline = previous.text.startswith('\n')
        rendered = serialize_block(header, body, leading_newline=leading_newline)
        block = TranscriptBlock(
            start=previous.start,
            text=rendered.text,
            layout=rendered.layout,
            title=rendered.title,
            key=mutation.key,
            item_kind=mutation.item_kind,
            header=header,
            body=body,
            status=(
                TranscriptItemStatus.TERMINAL
                if mutation.kind is TranscriptMutationKind.FINALIZE
                else TranscriptItemStatus.ACTIVE
            ),
            semantic_digest=_digest(rendered.text),
        )
        if block.semantic_digest == previous.semantic_digest:
            return None

        index = self.blocks.index(previous)
        delta = len(block.text) - len(previous.text)
        self.blocks[index] = block
        self.by_key[mutation.key] = block
        for following in self.blocks[index + 1 :]:
            following.start += delta
        self.expected_size += delta
        return TranscriptEdit(previous.start, previous.end, block.text, block, previous)

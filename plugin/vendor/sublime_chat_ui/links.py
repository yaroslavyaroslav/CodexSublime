"""Helpers for opening local Markdown file links from a transcript."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

_ENCODED_POSITION_RE = re.compile(r"^(?P<path>.*?):(?P<row>\d+)(?::(?P<col>\d+))?$")


@dataclass(frozen=True)
class MarkdownLink:
    """An inline Markdown link found in a single logical line."""

    destination: str
    begin: int
    end: int


def _is_escaped(text: str, point: int) -> bool:
    backslashes = 0
    point -= 1
    while point >= 0 and text[point] == "\\":
        backslashes += 1
        point -= 1
    return backslashes % 2 == 1


def _closing_bracket(text: str, begin: int) -> int | None:
    depth = 1
    for point in range(begin + 1, len(text)):
        if _is_escaped(text, point):
            continue
        if text[point] == "[":
            depth += 1
        elif text[point] == "]":
            depth -= 1
            if depth == 0:
                return point
    return None


def _inline_destination(text: str, open_paren: int) -> tuple[str, int] | None:
    destination_begin = open_paren + 1
    if destination_begin >= len(text):
        return None

    if text[destination_begin] == "<":
        for point in range(destination_begin + 1, len(text)):
            if text[point] == ">" and not _is_escaped(text, point):
                close_paren = point + 1
                if close_paren < len(text) and text[close_paren] == ")":
                    return text[destination_begin + 1 : point], close_paren
                return None
        return None

    depth = 1
    for point in range(destination_begin, len(text)):
        if _is_escaped(text, point):
            continue
        if text[point] == "(":
            depth += 1
        elif text[point] == ")":
            depth -= 1
            if depth == 0:
                return text[destination_begin:point], point
    return None


def markdown_link_at(text: str, point: int) -> MarkdownLink | None:
    """Return the inline link containing *point*, including nested path parens."""

    search_from = 0
    while True:
        begin = text.find("[", search_from)
        if begin < 0:
            return None
        search_from = begin + 1

        if (begin > 0 and text[begin - 1] == "!") or _is_escaped(text, begin):
            continue

        close_bracket = _closing_bracket(text, begin)
        if close_bracket is None or close_bracket + 1 >= len(text):
            continue
        if text[close_bracket + 1] != "(":
            continue

        destination = _inline_destination(text, close_bracket + 1)
        if destination is None:
            continue

        value, end = destination
        if begin <= point <= end:
            return MarkdownLink(value, begin, end + 1)


def local_file_target(destination: str) -> str | None:
    """Normalize an absolute local link, retaining an encoded row/column."""

    value = destination.strip()
    if not value:
        return None

    if value.startswith("file://"):
        parsed = urlsplit(value)
        if parsed.netloc not in {"", "localhost"}:
            return None
        value = unquote(parsed.path)
        if parsed.fragment:
            match = re.fullmatch(r"L(\d+)(?:C(\d+))?", parsed.fragment)
            if match:
                value += ":" + match.group(1)
                if match.group(2):
                    value += ":" + match.group(2)
    elif "://" in value:
        return None
    else:
        value = unquote(value)

    value = os.path.expanduser(value)
    path = value
    position = ""
    match = _ENCODED_POSITION_RE.fullmatch(value)
    if match and os.path.isfile(match.group("path")):
        path = match.group("path")
        position = value[len(path) :]

    if not os.path.isabs(path) or not os.path.isfile(path):
        return None
    return path + position

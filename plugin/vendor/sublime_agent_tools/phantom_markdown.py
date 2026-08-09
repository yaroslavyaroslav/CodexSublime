"""Markdown rendering for phantoms with an optional mdpopups fast path."""

from __future__ import annotations

import html
import re
from typing import Any


_FENCE = re.compile(r'```(?:[^\n]*)\n(.*?)```', re.DOTALL)
_INLINE_CODE = re.compile(r'`([^`]+)`')
_STRONG = re.compile(r'\*\*([^*]+)\*\*')
_EMPHASIS = re.compile(r'(?<!\*)\*([^*]+)\*(?!\*)')

ANNOTATION_WRAPPER_CLASS = 'sublime-agent-annotation'
ANNOTATION_CSS = f'''
.{ANNOTATION_WRAPPER_CLASS} {{
  display: block;
  margin: 0.35rem 0 0.5rem 0;
  padding: 0.55rem 0.7rem 0.1rem 0.7rem;
  border: 1px solid color(var(--background) blend(var(--foreground) 50%));
  border-radius: 0.25rem;
}}
'''


def minihtml(markdown: str) -> str:
    """Render the small Markdown subset needed by explanatory phantoms."""

    escaped = html.escape(markdown, quote=True)
    escaped = _FENCE.sub(lambda match: f'<pre><code>{match.group(1)}</code></pre>', escaped)
    escaped = _INLINE_CODE.sub(r'<code>\1</code>', escaped)
    escaped = _STRONG.sub(r'<strong>\1</strong>', escaped)
    escaped = _EMPHASIS.sub(r'<em>\1</em>', escaped)

    blocks = []
    for block in re.split(r'\n\s*\n', escaped.strip()):
        if block.startswith('<pre>'):
            blocks.append(block)
        else:
            blocks.append(f'<p>{block.replace(chr(10), "<br>")}</p>')
    return (
        f'<body><style>{ANNOTATION_CSS}</style><div class="{ANNOTATION_WRAPPER_CLASS}">'
        + ''.join(blocks)
        + '</div></body>'
    )


def phantom_classes(sublime_module: Any) -> tuple[type[Any], type[Any], bool]:
    """Prefer mdpopups' public Markdown-aware classes when installed."""

    try:
        import mdpopups

        return mdpopups.Phantom, mdpopups.PhantomSet, True
    except ImportError:
        return sublime_module.Phantom, sublime_module.PhantomSet, False

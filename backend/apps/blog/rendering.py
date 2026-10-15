"""
Markdown -> HTML, then through a sanitiser that is not optional.

Two layers, deliberately redundant:

1. nh3 (Rust `ammonia`) strips every tag and attribute not on the allowlist
   below. No `<script>`, no `on*=` handlers, no `javascript:` URLs, no `<style>`.
2. The public pages ship a `script-src 'none'` CSP, so even a sanitiser bypass
   produces markup the browser refuses to execute.

Syntax highlighting is done here, at render time, by Pygments — which is why
the public pages need no JavaScript at all.
"""

import re

import markdown
import nh3

ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark", "small", "sub", "sup",
    "blockquote", "q", "cite",
    "ul", "ol", "li",
    "dl", "dt", "dd",
    "pre", "code", "kbd", "samp", "var",
    "a", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "div", "span", "section", "abbr", "details", "summary",
}

ALLOWED_ATTRIBUTES = {
    # No "rel" here on purpose: nh3 sets it itself from link_rel below, and
    # refuses to do both.
    "a": {"href", "title", "id"},
    # "task" / "task task--done" from _tasks() below.
    "li": {"id", "class"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "abbr": {"title"},
    "ol": {"start", "type"},
    "th": {"align", "colspan", "rowspan", "scope"},
    "td": {"align", "colspan", "rowspan"},
    "col": {"span"},
    "colgroup": {"span"},
    "details": {"open"},
    # Pygments emits <span class="k">; the ToC and footnote extensions emit ids.
    "span": {"class", "id"},
    "div": {"class", "id"},
    "section": {"class", "id"},
    "pre": {"class"},
    "code": {"class"},
    "sup": {"id"},
    "table": {"class"},
    "h1": {"id"}, "h2": {"id"}, "h3": {"id"},
    "h4": {"id"}, "h5": {"id"}, "h6": {"id"},
}

# No data:, no javascript:, no anything else.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

EXTENSIONS = [
    "extra",        # fenced code, tables, footnotes, attr_list, def_list, abbr
    "codehilite",   # Pygments, server side
    "sane_lists",
    "smarty",       # proper quotes and dashes
    "toc",
    "admonition",
]

EXTENSION_CONFIGS = {
    "codehilite": {"css_class": "codehilite", "guess_lang": False},
    "toc": {"permalink": False},
}

_TAG_RE = re.compile(r"<[^>]+>")

_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
_LIST_ITEM_RE = re.compile(r"^ {0,3}([-*+]|1[.)])\s")
# A task item as Python-Markdown leaves it: tight (<li>[ ] x) or loose
# (<li>\n<p>[ ] x). The marker is dropped and drawn by CSS from the class.
_TASK_RE = re.compile(r"<li>(\s*<p>)?\[([ xX])\]\s+")


def _separate_lists(text: str) -> str:
    """
    Put a blank line on both sides of a list that touches a line of text.

    Notes apps (Apple Notes included) export lists with no blank lines around
    them. Python-Markdown then folds a list into the paragraph above it, and a
    line straight after a list into its last item ("lazy continuation"). Both
    are what nobody typing in a notes app means. Ordered lists only count when
    they start at 1, as in CommonMark, so "2001. was a year" stays prose.
    Indented lines are left alone: they are nested items or continuations.
    """
    out = []
    fenced = False
    prev = ""
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced and line.strip() and prev.strip() and not line.startswith((" ", "\t")):
            is_item, prev_item = bool(_LIST_ITEM_RE.match(line)), bool(_LIST_ITEM_RE.match(prev))
            if is_item and not prev_item and not prev.startswith((" ", "\t")):
                out.append("")      # text, then a list
            elif prev_item and not is_item:
                out.append("")      # a list, then text
        out.append(line)
        prev = line
    return "\n".join(out)


def _tasks(html: str) -> str:
    """GFM checklists ("- [ ] x", "- [x] x") into classed list items."""
    def repl(m):
        cls = "task task--done" if m.group(2) in "xX" else "task"
        return '<li class="%s">%s' % (cls, m.group(1) or "")
    return _TASK_RE.sub(repl, html)


# The same items in the markdown source, for flipping one: optional
# blockquote markers, a bullet or number, then the box.
_SOURCE_TASK_RE = re.compile(r"^((?:[ \t]*>)*[ \t]*(?:[-*+]|\d+[.)])[ \t]+)\[([ xX])\](?=\s)")


def task_lines(text: str) -> list[int]:
    """Line numbers of the checklist items in `text`, in rendered order."""
    lines = []
    fenced = False
    for i, line in enumerate(text.splitlines()):
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced and _SOURCE_TASK_RE.match(line):
            lines.append(i)
    return lines


def set_task(text: str, n: int, done: bool) -> str | None:
    """
    Tick or untick the n-th checklist item (0-based) of `text`.

    Returns None if the source and the render disagree on how many items there
    are (an item inside an indented code block, raw <li class="task"> HTML):
    then n could name the wrong line, and guessing would edit the wrong card.
    """
    lines = task_lines(text)
    if not 0 <= n < len(lines) or len(lines) != render_markdown(text).count('<li class="task'):
        return None
    out = text.splitlines(keepends=True)
    i = lines[n]
    out[i] = _SOURCE_TASK_RE.sub(lambda m: m.group(1) + ("[x]" if done else "[ ]"), out[i], count=1)
    return "".join(out)


_WS_RE = re.compile(r"\s+")


def render_markdown(text: str, notes: bool = False) -> str:
    """
    Render untrusted-ish markdown to HTML that is safe to insert verbatim.

    notes=True is for the private journal: a single newline is a line break,
    as in a notes app, instead of being folded into the paragraph.
    """
    if not text:
        return ""
    extensions = EXTENSIONS + ["nl2br"] if notes else EXTENSIONS
    # A fresh converter per call: markdown.Markdown instances carry state
    # between runs (footnote counters, the ToC) and leak it into the next post.
    html = markdown.Markdown(
        extensions=extensions, extension_configs=EXTENSION_CONFIGS, output_format="html"
    ).convert(_separate_lists(text))
    html = _tasks(html)
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


def plain_text(html: str) -> str:
    """Tags out, whitespace collapsed. Used for summaries and word counts."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def word_count(text: str) -> int:
    stripped = plain_text(text)
    return len(stripped.split()) if stripped else 0


def reading_minutes(words: int) -> int:
    return max(1, round(words / 200)) if words else 0

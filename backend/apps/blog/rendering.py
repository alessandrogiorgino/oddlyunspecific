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
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "abbr": {"title"},
    "ol": {"start", "type"},
    "li": {"id"},
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
_WS_RE = re.compile(r"\s+")


def render_markdown(text: str) -> str:
    """Render untrusted-ish markdown to HTML that is safe to insert verbatim."""
    if not text:
        return ""
    # A fresh converter per call: markdown.Markdown instances carry state
    # between runs (footnote counters, the ToC) and leak it into the next post.
    html = markdown.Markdown(
        extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS, output_format="html"
    ).convert(text)
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

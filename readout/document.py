"""PDF loading, page rendering and speech-ready text extraction."""

import os
import re
from dataclasses import dataclass, field

from PIL import Image

try:
    import pymupdf as fitz
except ImportError:  # older PyMuPDF releases only ship the `fitz` name
    import fitz

_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
_SOFT_BREAK = re.compile(r"[,;:—–][\"'”’)\]]*$")
# Initials and common abbreviations that end in a period but not a sentence.
_NOT_AN_END = re.compile(r"^(?:[A-Z]|Mr|Mrs|Ms|Dr|Prof|St|Jr|Sr|vs|etc|e\.g|i\.e|No|Fig|p|pp)\.$")
MAX_SENTENCE = 48  # keeps captions readable when a PDF has no punctuation

# Expand ligatures (ﬁ, ﬂ, …) so the voice doesn't stumble over them.
_FLAGS = getattr(fitz, "TEXTFLAGS_WORDS", None)
if _FLAGS is not None:
    _FLAGS &= ~getattr(fitz, "TEXT_PRESERVE_LIGATURES", 0)


def _words(page, sort=True):
    kwargs = {"sort": sort}
    if _FLAGS is not None:
        kwargs["flags"] = _FLAGS
    return page.get_text("words", **kwargs)


@dataclass
class Token:
    """One spoken word and where it sits on the page (PDF points)."""

    text: str
    rects: list
    line_key: tuple  # (block, line)
    sentence: int = 0


@dataclass
class PageText:
    tokens: list = field(default_factory=list)
    speech: str = ""
    starts: list = field(default_factory=list)  # char offset of each token in `speech`
    sentences: list = field(default_factory=list)  # (first_token, last_token)
    line_rects: dict = field(default_factory=dict)


def build_page_text(words, page_height, skip_margins=False):
    if skip_margins:
        top, bottom = page_height * 0.06, page_height * 0.94
        words = [w for w in words if w[3] >= top and w[1] <= bottom]

    tokens = []
    i = 0
    while i < len(words):
        x0, y0, x1, y1, text, block, line = words[i][:7]
        rects = [(x0, y0, x1, y1)]
        key = (block, line)
        # Re-join words hyphenated across a line break: "infor-" + "mation".
        while text.endswith("-") and len(text) > 1 and i + 1 < len(words):
            nxt = words[i + 1]
            if nxt[5] != block or (nxt[5], nxt[6]) == key or not nxt[4][:1].islower():
                break
            text = text[:-1] + nxt[4]
            rects.append(tuple(nxt[:4]))
            i += 1
        tokens.append(Token(text, rects, key))
        i += 1

    pt = PageText(tokens=tokens)
    for tok in tokens:
        r = tok.rects[0]
        u = pt.line_rects.get(tok.line_key)
        pt.line_rects[tok.line_key] = (
            r if u is None else (min(u[0], r[0]), min(u[1], r[1]), max(u[2], r[2]), max(u[3], r[3]))
        )
    block_width = {}
    for (block, _line), r in pt.line_rects.items():
        block_width[block] = max(block_width.get(block, 0), r[2] - r[0])

    def ends_unit(a, b):
        """True when `a` closes a heading, list item or paragraph that lacks punctuation."""
        if _SENTENCE_END.search(a.text) or _SOFT_BREAK.search(a.text):
            return False
        if a.line_key[0] != b.line_key[0]:
            return True
        if a.line_key == b.line_key or not b.text[:1].isupper():
            return False
        line = pt.line_rects[a.line_key]
        return line[2] - line[0] < 0.75 * block_width[a.line_key[0]]  # short line, e.g. a TOC entry

    parts, pos, first = [], 0, 0
    for idx, tok in enumerate(tokens):
        if idx:
            sep = ". " if ends_unit(tokens[idx - 1], tok) else " "  # ". " makes the voice pause
            parts.append(sep)
            pos += len(sep)
        pt.starts.append(pos)
        parts.append(tok.text)
        pos += len(tok.text)

        tok.sentence = len(pt.sentences)
        length = idx - first + 1
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
        if (
            nxt is None
            or (_SENTENCE_END.search(tok.text) and not _NOT_AN_END.match(tok.text))
            or nxt.line_key[0] != tok.line_key[0]
            or ends_unit(tok, nxt)
            or length >= MAX_SENTENCE
            or (length >= MAX_SENTENCE // 2 and _SOFT_BREAK.search(tok.text))
        ):
            pt.sentences.append((first, idx))
            first = idx + 1

    pt.speech = "".join(parts)
    return pt


class PdfDocument:
    def __init__(self, path, skip_margins=False):
        self.path = os.path.abspath(path)
        self.password = None
        self._doc = fitz.open(self.path)
        self._skip = skip_margins
        self._cache = {}

    @property
    def needs_pass(self):
        return bool(self._doc.needs_pass)

    def authenticate(self, password):
        if self._doc.authenticate(password):
            self.password = password
            return True
        return False

    @property
    def page_count(self):
        return len(self._doc)

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def title(self):
        meta = self._doc.metadata or {}
        return (meta.get("title") or "").strip() or self.name

    def page_size(self, index):
        r = self._doc[index].rect
        return r.width, r.height

    def render(self, index, zoom):
        pix = self._doc[index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    def text(self, index):
        if index not in self._cache:
            page = self._doc[index]
            self._cache[index] = build_page_text(_words(page), page.rect.height, self._skip)
        return self._cache[index]

    def set_skip_margins(self, value):
        if value != self._skip:
            self._skip = value
            self._cache.clear()

    def close(self):
        self._doc.close()


def count_words(path, password=None):
    """Word count per page, used for the time-remaining estimate.

    Opens its own handle so it can run off the UI thread.
    """
    doc = fitz.open(path)
    try:
        if password:
            doc.authenticate(password)
        return [len(_words(page, sort=False)) for page in doc]
    finally:
        doc.close()

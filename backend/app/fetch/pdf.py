"""F9: an uploaded PDF as the analysed article.

The pipeline reads the article as markdown with its links kept (`[anchor](url)`): sources are links in the same
sentence or paragraph as a claim. A PDF has no markup, so this module rebuilds that shape:
- the page text (pypdf), hard line wraps joined into paragraphs (the sentence splitter breaks units at every newline);
- printed URLs ("see https://who.int/...") become links;
- clickable link annotations become links on their anchor text. A link whose anchor cannot be found in the text is
  dropped: placing it anywhere else would credit a source to a claim it was never attached to.
"""
from __future__ import annotations

import io
import logging
import re
from statistics import median

from .fetcher import FetchResult, _finish

log = logging.getLogger("citetrace.pdf")
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 80
_URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_END = re.compile(r"[.!?:…\"”’)]$")


def _join_lines(text: str) -> str:
    """Joins wrapped lines into paragraphs. A paragraph ends at a blank line, or at a line that ends a sentence and is
    clearly shorter than the page's typical line (the last line of a paragraph rarely fills the width)."""
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    full = [len(ln) for ln in lines if ln]
    typical = median(full) if full else 0
    paras: list[str] = []
    cur = ""
    for ln in lines:
        if not ln:
            if cur:
                paras.append(cur)
            cur = ""
            continue
        if cur.endswith("-") and ln[:1].islower():         # word hyphenated across the wrap
            cur = cur[:-1] + ln
        else:
            cur = f"{cur} {ln}" if cur else ln
        if _END.search(ln) and len(ln) < 0.75 * typical:
            paras.append(cur)
            cur = ""
    if cur:
        paras.append(cur)
    return "\n\n".join(paras)


def _link_printed_urls(text: str) -> str:
    def repl(m: re.Match) -> str:
        url = m.group(0).rstrip(".,;:")
        return f"[{url}]({url}){m.group(0)[len(url):]}"
    return _URL.sub(repl, text)


def _annotation_links(page) -> list[tuple[str, tuple[float, float, float, float]]]:
    out = []
    for ref in page.get("/Annots") or []:
        try:
            a = ref.get_object()
            if a.get("/Subtype") != "/Link":
                continue
            uri = (a.get("/A") or {}).get("/URI")
            if not uri or not str(uri).startswith(("http://", "https://")):
                continue
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            out.append((str(uri), (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))))
        except Exception:          # a broken annotation is skipped, never fatal
            continue
    return out


def _anchor(frags: list[tuple[str, float, float, float]], rect: tuple[float, float, float, float]) -> str:
    """Text under a link rectangle. pypdf reports where a text piece STARTS, not where each character is, so the
    characters are placed with an average width of half the font size and the cut is widened to whole words."""
    x0, y0, x1, y1 = rect
    parts = []
    for text, x, y, size in frags:
        if not text.strip() or not (y0 - 2 <= y <= y1 + 2):
            continue
        w = max(size, 1.0) * 0.5
        i0, i1 = max(0, round((x0 - x) / w)), min(len(text), round((x1 - x) / w))
        if i1 <= i0:
            continue
        while i0 > 0 and not text[i0 - 1].isspace():
            i0 -= 1
        while i1 < len(text) and not text[i1].isspace():
            i1 += 1
        parts.append(text[i0:i1])
    return " ".join(" ".join(parts).split())


def _page_text(page) -> str:
    """Page text rebuilt from pypdf's text pieces (a vertical gap clearly larger than the line spacing = new paragraph),
    printed URLs and link annotations turned into markdown links."""
    links = _annotation_links(page)
    frags: list[tuple[str, float, float, float]] = []

    def visit(text, cm, tm, _font, size):
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        frags.append((text, x, y, size * abs(tm[0] or 1) * abs(cm[0] or 1)))

    plain = page.extract_text(visitor_text=visit) or ""
    ys = [y for t, _, y, _ in frags if t.strip()]
    steps = [a - b for a, b in zip(ys, ys[1:]) if a - b > 0.5]
    spacing = sorted(steps)[len(steps) // 4] if steps else 0       # lower quartile: plain line steps outnumber gaps
    pieces, last_y = [], None
    for text, _, y, _ in frags:
        if not text.strip():
            pieces.append(text)
            continue
        if last_y is not None and spacing and last_y - y > 1.5 * spacing:
            pieces.append("\n\n")
        pieces.append(text)
        last_y = y
    rebuilt = "".join(pieces)
    text = _link_printed_urls(_join_lines(rebuilt if rebuilt.strip() else plain))
    for uri, rect in links:
        anchor = _anchor(frags, rect)
        if len(anchor) < 3 or anchor.startswith(("http://", "https://")):   # printed URLs are linked already
            continue
        taken = [m.span() for m in _MD_LINK.finditer(text)]
        at = next((i for i in (m.start() for m in re.finditer(re.escape(anchor), text))
                   if not any(s <= i < e for s, e in taken)), None)
        if at is not None:
            text = f"{text[:at]}[{anchor}]({uri}){text[at + len(anchor):]}"
    return text


def pdf_to_article(data: bytes, url: str, filename: str | None = None) -> FetchResult:
    """-> the article as `FetchResult` (status ok, or blocked with a reason the user can read)."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")                       # many PDFs are "encrypted" with an empty user password
        pages = [_page_text(p) for p in reader.pages[:MAX_PAGES]]
        meta_title = reader.metadata.title if reader.metadata else None
    except Exception as exc:
        log.info("PDF %s unreadable: %s", filename, type(exc).__name__)
        return FetchResult(url, "link", "blocked", via="pdf", error="not a readable PDF")
    title = (str(meta_title).strip() if meta_title else "") or (filename or "").removesuffix(".pdf") or None
    res = _finish(url, "link", "\n\n".join(p for p in pages if p.strip()), "pdf", html=None, title=title, final_url=None)
    if res.status != "ok" and res.error and "too short" in res.error:
        res.error = "the PDF has no text layer (scanned image?)"
    return res

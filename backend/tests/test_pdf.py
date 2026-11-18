"""F9: uploaded PDF -> article markdown (links kept) -> the normal pipeline. No network: PDFs are built in memory."""
import io

import test_jobs as tj
import test_run as tr
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link

from app.fetch.pdf import _join_lines, pdf_to_article
from app.pipeline.run import analyze


def make_pdf(lines: list[str], links: list[tuple[tuple[float, float, float, float], str]] = ()) -> bytes:
    """One page, Helvetica 11 pt, one line every 14 pt from y=760 down; `links` = (rect, url) link annotations."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = "".join(f"BT /F1 11 Tf 72 {760 - 14 * i} Td ({esc(t)}) Tj ET\n" for i, t in enumerate(lines)).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    if not links:
        return bytes(out)
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(bytes(out))))
    for rect, url in links:
        w.add_annotation(0, Link(rect=rect, url=url))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


LINES = [
    "The ministry said on Tuesday that the new programme will expand coverage to",
    "40 districts by 2027, the official report states in its summary of the plan.",
    "",
    "Western officials have privately admitted that the drone attacks were staged,",
    "according to a report published at https://news-front.net/report-123 last week.",
    "",
    "Local residents saw no damage at all in the district after the alleged attack.",
]
# "the official report" on line 2 (y = 746): Helvetica 11 pt, starts after "40 districts by 2027, " (~110 pt from x=72)
REPORT_LINK = ((175, 742, 290, 757), "https://ministry.gov.uk/report")


def test_wrapped_lines_become_paragraphs_and_hyphenation_is_undone():
    text = _join_lines("This is a long line of text that wraps over to the next\nline and ends here with a full stop now.\n"
                       "Short end.\nA second para-\ngraph that is long enough to count as a full width line of text.")
    assert text.split("\n\n") == [
        "This is a long line of text that wraps over to the next line and ends here with a full stop now. Short end.",
        "A second paragraph that is long enough to count as a full width line of text."]


def test_pdf_text_keeps_printed_urls_and_clickable_links_as_markdown():
    art = pdf_to_article(make_pdf(LINES, [REPORT_LINK]), "pdf:abc", "brief.pdf")
    assert art.status == "ok" and art.via == "pdf" and art.html is None and art.title == "brief"
    assert "[https://news-front.net/report-123](https://news-front.net/report-123) last week." in art.text
    paras = art.text.split("\n\n")
    assert len(paras) == 3 and "40 districts by" in paras[0] and "news-front.net" in paras[1]
    # the clickable link sits on (about) its anchor words: character positions in a PDF are estimates, the sentence is right
    assert "the official report](https://ministry.gov.uk/report) states" in paras[0]


def test_unreadable_or_textless_pdf_is_blocked_with_a_reason():
    assert pdf_to_article(b"%PDF-1.4 garbage", "pdf:x").status == "blocked"
    empty = pdf_to_article(make_pdf([]), "pdf:y")
    assert empty.status == "blocked" and "no text layer" in empty.error


def test_pipeline_runs_on_a_pdf_article_without_fetching_it(pages):
    art = pdf_to_article(make_pdf(LINES, [REPORT_LINK]), "pdf:abc", "brief.pdf")

    class NoArticleFetch(tr.FakeFetcher):
        def fetch_article(self, url):
            raise AssertionError("an uploaded PDF must not be fetched")

    rdap = tr.FakeRdap({"news-front.net": 55, "ministry.gov.uk": 3000})
    r = analyze("pdf:abc", fetcher=NoArticleFetch(None, pages), llm=tr.router(tr.EXTRACT, tr.verifier),
                settings=tr.SETTINGS, rdap=rdap, article=art).result
    assert r["article"]["url"] == "pdf:abc" and r["article"]["domain"] == ""
    by_text = {c["text"][:20]: c for c in r["claims"]}
    assert by_text["The ministry said on"]["verdict"] == "supported"
    assert {s["url"] for s in r["sources"]} >= {"https://ministry.gov.uk/report", "https://news-front.net/report-123"}
    assert all(d["domain"] for d in r["domains"]) and "" not in rdap.looked        # no empty "own domain" row or lookup
    assert not any(e["id"] == "e-a-host" for e in r["graph"]["edges"])


def test_upload_endpoint_starts_one_job_per_file_and_rejects_non_pdfs(client, jobs):
    seen = []

    class Fake(tj.Fake):
        def __call__(self, url, **kw):
            seen.append((url, kw.get("article")))
            return super().__call__(url, **kw)

    jobs.analyze_fn = Fake(tj.real_output())
    pdf = make_pdf(LINES)
    r = client.post("/api/analyses/pdf?name=brief.pdf", content=pdf, headers={"Content-Type": "application/pdf"})
    assert r.status_code == 202, r.text
    job = r.json()["job_id"]
    assert tj.done(client, job)["status"] == "done"
    url, art = seen[0]
    assert url.startswith("pdf:") and art.status == "ok" and art.title == "brief"
    again = client.post("/api/analyses/pdf?name=other-name.pdf", content=pdf)
    assert again.json() == {"job_id": job, "status": "done", "cached": True}
    assert client.post("/api/analyses/pdf", content=b"<html>hi</html>").status_code == 422
    assert client.post("/api/analyses/pdf", content=make_pdf([])).json()["error"] == "invalid_pdf"


jobs = tj.jobs
pages = tr.pages

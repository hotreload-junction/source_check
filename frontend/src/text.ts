// Locate claims in article.text_markdown and split it into highlight segments.
// Contract offsets are Unicode code points; JS strings are UTF-16, so convert and fall back to search.

export interface Mark { start: number; end: number; c: string; id: string }
export interface Seg { t: string; c: string | null; id: string | null }

export function locate(text: string, cpStart: number | null, cpEnd: number | null, needle: string): [number, number] | null {
  if (cpStart != null && cpEnd != null && cpEnd > cpStart) {
    const cps = [...text];
    const start = cps.slice(0, cpStart).join('').length;
    const span = cps.slice(cpStart, cpEnd).join('');
    if (span === needle) return [start, start + span.length];
  }
  const i = needle ? text.indexOf(needle) : -1;
  return i >= 0 ? [i, i + needle.length] : null;
}

export function segments(text: string, marks: Mark[]): Seg[] {
  const out: Seg[] = [];
  let p = 0;
  [...marks].sort((a, b) => a.start - b.start).forEach((m) => {
    if (m.start < p) return; // overlapping claim, keep the first
    if (m.start > p) out.push({ t: text.slice(p, m.start), c: null, id: null });
    out.push({ t: text.slice(m.start, m.end), c: m.c, id: m.id });
    p = m.end;
  });
  if (p < text.length) out.push({ t: text.slice(p), c: null, id: null });
  return out.map((s) => ({ ...s, t: plain(s.t) }));
}

// Display only: drop markdown syntax that would read as noise (links keep their text).
export function plain(md: string): string {
  return md
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*|__/g, '');
}

// Only http(s) links become clickable. Source URLs come from scraped pages and search/agent results; a `javascript:` or
// `data:` URL in an <a href> would run in our origin when clicked. Anything else is shown as text, not linked.
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : undefined;
  } catch {
    return undefined;
  }
}

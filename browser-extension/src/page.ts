// What is read from the page. `readPage` is injected into the tab with chrome.scripting.executeScript: it is
// serialised on its own, so it must not use anything from outside its body.
export interface PageData {
  url: string;
  title: string;
  html: string;
  selection: string;
}

export function readPage(): PageData {
  const LIMIT = 5_500_000;         // backend accepts 6 M characters
  let html = document.documentElement.outerHTML;
  if (html.length > LIMIT) {
    // big pages: drop what never holds article text. Scripts stay (analytics / AdSense IDs are an infrastructure signal).
    const copy = document.documentElement.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('style, svg, noscript, iframe, template, link[rel="stylesheet"], img[src^="data:"]').forEach((e) => e.remove());
    html = copy.outerHTML;
  }
  if (html.length > LIMIT) {
    const copy = document.documentElement.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('style, svg, noscript, iframe, template, script:not([src])').forEach((e) => e.remove());
    html = copy.outerHTML.slice(0, LIMIT);
  }
  const sel = window.getSelection ? String(window.getSelection() ?? '') : '';
  return { url: location.href, title: document.title, html, selection: sel };
}

export const MIN_SELECTION = 12;      // backend MIN_SELECTION_CHARS
export const MAX_SELECTION = 1500;    // backend MAX_SELECTION_CHARS

export const cleanSelection = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, ' ').trim();

/** Why a selection cannot be checked, or null when it can. */
export function selectionProblem(sel: string): string | null {
  if (!sel) return 'Select the sentence you want to check on the page first.';
  if (sel.length < MIN_SELECTION) return 'The selection is too short to find in the article. Select the whole sentence.';
  if (sel.length > MAX_SELECTION) return 'The selection is longer than one claim. Select a single sentence or two.';
  return null;
}

/** Pages no extension may read (browser UI, extension stores, local files without permission, the PDF viewer). */
export function unreadableReason(url: string | undefined): string | null {
  if (!url) return 'There is no page in this tab.';
  if (/^(chrome|edge|brave|opera|vivaldi|about|chrome-extension|moz-extension|view-source|devtools):/i.test(url))
    return 'This is a browser page, not an article.';
  if (/^https:\/\/(chrome\.google\.com\/webstore|chromewebstore\.google\.com|microsoftedge\.microsoft\.com\/addons|addons\.mozilla\.org)/i.test(url))
    return 'Browsers do not let extensions read extension store pages.';
  if (/^file:/i.test(url)) return 'Local files cannot be checked. Open the article from the web.';
  if (!/^https?:/i.test(url)) return 'Only web pages (http/https) can be checked.';
  if (/\.pdf($|[?#])/i.test(url)) return 'This is a PDF. Upload it on the Source Check website instead.';
  return null;
}

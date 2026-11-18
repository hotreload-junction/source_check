// Starting a check: read the page in the tab, send it to the backend (POST /api/analyses/page), remember the job.
// Used by the background (toolbar icon, context menu) and by the side panel's buttons.
import { getApiBase } from './config';
import { cleanSelection, readPage, selectionProblem, unreadableReason, type PageData } from './page';
import { putState, type CheckState } from './state';

export interface StartResponse { job_id: string; status: string; cached: boolean }

export interface CheckRequest {
  tabId: number;
  url?: string;
  title?: string;
  /** true = "check this claim": the selected passage is the focus claim. */
  selection: boolean;
  /** the context menu's copy of the selection, used when the page's own selection is gone (e.g. inside a frame) */
  selectionText?: string;
}

async function errorText(res: Response, base: string): Promise<string> {
  let detail = '';
  try {
    const b = await res.json();
    detail = typeof b.detail === 'string' ? b.detail : typeof b.error === 'string' ? b.error : '';
  } catch { /* not JSON */ }
  if (res.status === 429) return `The server is busy: ${detail || 'try again in a minute'}.`;
  if (res.status === 404 || res.status === 405) return `The server at ${base} does not support the extension yet (no /api/analyses/page).`;
  if (res.status === 413) return 'This page is too large to send.';
  return detail || `The server answered with an error (HTTP ${res.status}).`;
}

export async function startCheck(req: CheckRequest): Promise<CheckState> {
  const { tabId } = req;
  const base: CheckState = { tabId, url: req.url ?? '', title: req.title ?? '', selection: null, phase: 'reading', at: Date.now() };
  const set = async (patch: Partial<CheckState>) => {
    Object.assign(base, patch, { at: Date.now() });
    await putState({ ...base });
    return { ...base };
  };
  const fail = (error: string) => set({ phase: 'error', error });

  await set({});
  const blocked = unreadableReason(req.url);
  if (blocked) return fail(blocked);

  let page: PageData | undefined;
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId }, func: readPage });
    page = res?.result as PageData | undefined;
  } catch (e) {
    const msg = String((e as Error)?.message ?? e);
    return fail(/permission|access|Cannot access/i.test(msg)
      ? 'Source Check may not read this page yet. Click the Source Check icon in the toolbar (or right-click the page) to allow it.'
      : `The page could not be read: ${msg}`);
  }
  if (!page?.html) return fail('The page could not be read.');

  let selection: string | null = null;
  if (req.selection) {
    selection = cleanSelection(page.selection) || cleanSelection(req.selectionText);
    const problem = selectionProblem(selection);
    if (problem) return fail(problem);
  }
  await set({ url: page.url, title: page.title || base.title, selection, phase: 'starting' });

  const api = await getApiBase();
  let res: Response;
  try {
    res = await fetch(`${api}/api/analyses/page`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: page.url, html: page.html, title: page.title || null, selection }),
    });
  } catch {
    return fail(`Cannot reach the Source Check server at ${api}. Check your connection or the server address in the extension options.`);
  }
  if (!res.ok) return fail(await errorText(res, api));
  let body: StartResponse;
  try { body = await res.json(); } catch { return fail('The server sent an unreadable answer.'); }
  if (!body?.job_id) return fail('The server did not start a check.');
  return set({ phase: 'started', jobId: body.job_id, cached: !!body.cached });
}

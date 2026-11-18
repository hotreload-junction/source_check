// Which backend the extension talks to. Default: the deployed site; the options page can point it elsewhere
// (preview, a local backend). Stored in chrome.storage.sync so it follows the reader's browser profile.
export const DEFAULT_API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) || 'https://sourcecheck.tech';
const KEY = 'apiBase';

/** "https://host[:port][/path]" without a trailing slash, or null when it is not an http(s) address. */
export function normaliseApiBase(raw: string): string | null {
  let u: URL;
  try { u = new URL(raw.trim()); } catch { return null; }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  if (u.username || u.password || u.search || u.hash) return null;
  return `${u.protocol}//${u.host}${u.pathname.replace(/\/+$/, '')}`;
}

/** Host permission pattern for an http(s) address: "https://example.com/*". */
export const originPattern = (url: string): string | null => {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? `${u.protocol}//${u.hostname}/*` : null;
  } catch { return null; }
};

export async function getApiBase(): Promise<string> {
  try {
    const v = (await chrome.storage.sync.get(KEY))[KEY];
    return (typeof v === 'string' && normaliseApiBase(v)) || DEFAULT_API_BASE;
  } catch { return DEFAULT_API_BASE; }
}

export const setApiBase = (base: string | null) =>
  base ? chrome.storage.sync.set({ [KEY]: base }) : chrome.storage.sync.remove(KEY);

/** The web app's address for a result, to open it in a full tab or share it. */
export const resultLink = (base: string, jobId: string) => `${base}/#/trace?job=${encodeURIComponent(jobId)}`;

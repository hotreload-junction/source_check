// The check of each tab: written by whoever starts it (toolbar icon / context menu in the background, the panel's own
// buttons), read by the side panel. chrome.storage.session: survives the service worker going to sleep, gone when
// the browser closes, never synced.
export type Phase = 'reading' | 'starting' | 'started' | 'error';

export interface CheckState {
  tabId: number;
  url: string;              // the page that was read (compared with the tab's current address)
  title: string;
  selection: string | null; // "check this claim": the selected passage
  phase: Phase;
  jobId?: string;
  cached?: boolean;
  error?: string;
  at: number;               // when this phase was entered (ms): the panel follows only newer states
}

const PREFIX = 'check:';
export const stateKey = (tabId: number) => `${PREFIX}${tabId}`;
export const isStateKey = (k: string) => k.startsWith(PREFIX);

export async function getState(tabId: number): Promise<CheckState | null> {
  const k = stateKey(tabId);
  return ((await chrome.storage.session.get(k))[k] as CheckState | undefined) ?? null;
}

export const putState = (s: CheckState) => chrome.storage.session.set({ [stateKey(s.tabId)]: s });
export const dropState = (tabId: number) => chrome.storage.session.remove(stateKey(tabId));

/** Same document, ignoring the #fragment (in-page anchors and app routers do not change the article). */
export function sameDocument(a: string | undefined | null, b: string | undefined | null): boolean {
  if (!a || !b) return false;
  return a.split('#')[0] === b.split('#')[0];
}

// Side panel: follows the active tab. Home = what can be done with this page; a started check switches to the web
// app's own result view (graph, claims, evidence), bundled from ../frontend/src and pointed at the backend.
import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from '@app/App';
import { About } from '@app/About';
import { Methodology } from '@app/Methodology';
import { setApiBase } from '@app/api';
import '@app/styles.css';
import { getApiBase } from '../config';
import { getState, sameDocument, stateKey, type CheckState } from '../state';
import { Home } from './Home';

const traceHash = (jobId: string) => `#/trace?job=${encodeURIComponent(jobId)}`;

// sidepanel.html?tab=<id>: follow one tab instead of the active one (open the panel in a full tab for development)
const PINNED = Number(new URLSearchParams(location.search).get('tab')) || null;

function useActiveTab(): chrome.tabs.Tab | null {
  const [tab, setTab] = useState<chrome.tabs.Tab | null>(null);
  useEffect(() => {
    if (PINNED != null) {
      const get = () => chrome.tabs.get(PINNED).then(setTab, () => setTab(null));
      void get();
      const onUpdated = (id: number) => { if (id === PINNED) void get(); };
      chrome.tabs.onUpdated.addListener(onUpdated);
      return () => chrome.tabs.onUpdated.removeListener(onUpdated);
    }
    let windowId: number | undefined;
    const refresh = () => chrome.tabs.query({ active: true, currentWindow: true }).then(([t]) => {
      windowId = t?.windowId;
      setTab(t ?? null);
    });
    void refresh();
    const onActivated = (info: chrome.tabs.TabActiveInfo) => { if (windowId == null || info.windowId === windowId) void refresh(); };
    const onUpdated = (_id: number, change: chrome.tabs.TabChangeInfo, t: chrome.tabs.Tab) => {
      if (t.active && (windowId == null || t.windowId === windowId) && (change.url || change.title || change.status === 'complete')) setTab(t);
    };
    chrome.tabs.onActivated.addListener(onActivated);
    chrome.tabs.onUpdated.addListener(onUpdated);
    return () => { chrome.tabs.onActivated.removeListener(onActivated); chrome.tabs.onUpdated.removeListener(onUpdated); };
  }, []);
  return tab;
}

function useCheckState(tabId: number | undefined): CheckState | null {
  const [state, setState] = useState<CheckState | null>(null);
  useEffect(() => {
    if (tabId == null) { setState(null); return; }
    let live = true;
    void getState(tabId).then((s) => { if (live) setState(s); });
    const onChanged = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area !== 'session') return;
      const c = changes[stateKey(tabId)];
      if (c) setState((c.newValue as CheckState | undefined) ?? null);
    };
    chrome.storage.onChanged.addListener(onChanged);
    return () => { live = false; chrome.storage.onChanged.removeListener(onChanged); };
  }, [tabId]);
  return state;
}

function Root({ apiBase }: { apiBase: string }) {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  const tab = useActiveTab();
  const raw = useCheckState(tab?.id);
  const state = raw && (raw.phase === 'reading' || sameDocument(raw.url, tab?.url)) ? raw : null;   // a check of an earlier page is stale

  // show the result as soon as a check of THIS tab starts; switching tabs shows that tab's result (or Home)
  const shown = useRef<string | null>(null);
  useEffect(() => {
    const target = state?.phase === 'started' && state.jobId ? `${state.jobId}@${state.at}` : null;
    if (target === shown.current) return;
    shown.current = target;
    if (state?.phase === 'started' && state.jobId) location.hash = traceHash(state.jobId);
    else if (location.hash.startsWith('#/trace')) location.hash = '#/';
  }, [state?.phase, state?.jobId, state?.at, tab?.id]);

  if (hash.startsWith('#/methodology')) return <Methodology />;
  if (hash.startsWith('#/about')) return <About />;
  if (hash.startsWith('#/trace')) {
    const q = new URLSearchParams(hash.split('?')[1] ?? '');
    return <App key={hash} initial={{ url: q.get('url') ?? undefined, demo: q.get('demo') ?? undefined, question: q.get('q') ?? undefined, job: q.get('job') ?? undefined }} />;
  }
  return <Home tab={tab} state={state} apiBase={apiBase} onShow={(id) => { location.hash = traceHash(id); }} />;
}

void getApiBase().then((base) => {
  setApiBase(base);
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Root apiBase={base} />
    </StrictMode>,
  );
});

// the server address can change on the options page while the panel is open
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && changes.apiBase) location.reload();
});

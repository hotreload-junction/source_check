// API client for contracts/api.md. `npm run dev:mock` swaps in mockApi (no backend).
import { mockApi } from './mock';
import type { Analysis, Claim, DemoCase, GraphEdge, GraphNode, Methodology, Progress, StartResponse } from './types';

export interface Handlers {
  progress: (p: Progress) => void;
  node: (n: GraphNode) => void;
  edge: (e: GraphEdge) => void;
  claim: (c: Claim) => void;
  done: (a: Analysis) => void;
  error: (message: string) => void;
}

export interface Api {
  mock: boolean;
  demo(): Promise<DemoCase[]>;
  start(url: string, force?: boolean, question?: string): Promise<StartResponse>;
  startPdf(file: File): Promise<StartResponse>; // F9: the file itself is the request body
  get(jobId: string): Promise<Analysis>;
  methodology(): Promise<Methodology>;
  events(jobId: string, h: Handlers): () => void; // returns close()
}

async function json<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>;
  let detail = res.statusText;
  try {
    const b = await res.json();
    detail = b.detail ?? b.error ?? detail;
    if (Array.isArray(detail)) detail = 'Please enter a valid article URL.'; // FastAPI 422 body
  } catch { /* not JSON */ }
  throw new Error(res.status === 429 ? `Busy: ${detail}` : String(detail));
}

const realApi: Api = {
  mock: false,
  // The three demo cases ship inside the bundle (precomputed, no backend needed). Once the backend lists its own cases they win.
  demo: () => fetch('/api/demo').then((r) => json<DemoCase[]>(r)).then((ds) => (ds.length ? ds : mockApi.demo()), () => mockApi.demo()),
  start: (url, force = false, question) =>
    fetch('/api/analyses', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(question ? { url, force, question } : { url, force }) }).then((r) => json<StartResponse>(r)),
  startPdf: async (file) => {
    let r: Response;
    try {
      r = await fetch(`/api/analyses/pdf?name=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: file });
    } catch {
      throw new Error('Could not reach the server. Is the backend running?');
    }
    try {
      return await json<StartResponse>(r);
    } catch (e) {
      const m = String((e as Error).message || '');
      if (m && m !== r.statusText) throw e;        // the backend explained itself
      throw new Error(`Upload failed (HTTP ${r.status}).${r.status === 404 || r.status === 405 ? ' The backend is probably outdated: restart it.' : ''}`);
    }
  },
  get: (id) => (id.startsWith('example-job') ? mockApi.get(id) : fetch(`/api/analyses/${encodeURIComponent(id)}`).then((r) => json<Analysis>(r))),
  methodology: () => fetch('/api/methodology').then((r) => json<Methodology>(r)),
  events(id, h) {
    let finished = false;
    let poll: ReturnType<typeof setInterval> | null = null;
    const es = typeof EventSource !== 'undefined' ? new EventSource(`/api/analyses/${encodeURIComponent(id)}/events`) : null;
    const close = () => {
      es?.close();
      if (poll) clearInterval(poll);
    };
    const finish = (a: Analysis) => {
      if (finished) return;
      finished = true;
      close();
      if (a.status === 'failed') h.error(a.error || 'Analysis failed');
      else if (a.result) h.done(a);
      else realApi.get(id).then(h.done, (e) => h.error(String(e.message || e)));
    };
    // Fallback without SSE: poll every 2 s (contract).
    const startPolling = () => {
      if (poll || finished) return;
      poll = setInterval(() => {
        realApi.get(id).then((a) => {
          h.progress(a.progress);
          if (a.status === 'done' || a.status === 'failed') finish(a);
        }, () => { /* keep polling */ });
      }, 2000);
    };
    const on = <T,>(type: string, fn: (d: T) => void) =>
      es?.addEventListener(type, (ev) => {
        let d: T;
        try { d = JSON.parse((ev as MessageEvent).data); } catch { return; } // ignore malformed events
        fn(d);
      });
    on<Progress>('progress', h.progress);
    on<GraphNode>('node', h.node);
    on<GraphEdge>('edge', h.edge);
    on<Claim>('claim', h.claim);
    on<Analysis>('done', finish);
    on<{ message: string }>('error', (d) => {
      if (!d || !d.message || finished) return;
      finished = true;
      close();
      h.error(d.message);
    });
    if (es) es.onerror = () => { es.close(); startPolling(); };
    else startPolling();
    return () => { finished = true; close(); };
  },
};

export const api: Api = import.meta.env.MODE === 'mock' ? mockApi : realApi;

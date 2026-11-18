// Mock backend for `npm run dev:mock`: contract examples + fictional fixtures, no network.
// Demo buttons load cached results (force = replay the Pravda example as a live SSE run); any other URL is refused.
import demo from '../../contracts/examples/demo.json';
import pravda from '../../contracts/examples/analysis_done.json';
import focusCase from '../../contracts/examples/analysis_focus.json';
import methodology from '../../contracts/examples/methodology.json';
import blog from './mocks/blog.json';
import wire from './mocks/wire.json';
import type { Api } from './api';
import type { Analysis, DemoCase, Methodology } from './types';

const JOBS: Record<string, Analysis> = {
  'example-job-1': pravda as unknown as Analysis,
  'example-job-3': wire as unknown as Analysis,
  'example-job-4': blog as unknown as Analysis,
  'example-job-focus': focusCase as unknown as Analysis, // F6: any URL with a question
};
const LIVE = 'mock-live';
const wait = <T,>(v: T, ms = 150) => new Promise<T>((res) => setTimeout(() => res(v), ms));

export const mockApi: Api = {
  mock: true,
  demo: () => wait(demo as DemoCase[]),
  start(url, force = false, question) {
    const d = (demo as DemoCase[]).find((x) => x.url === url);
    // Any other URL used to replay the Pravda example, which looked like a real (wrong) result for the reader's article.
    if (!d) return Promise.reject(new Error('Mock mode (no backend): only the demo articles work here. Run "npm run dev" in frontend/ with the backend on :8000 to analyse this URL.'));
    if (question) return wait({ job_id: 'example-job-focus', status: 'done' as const, cached: true });
    return wait(!force ? { job_id: d.job_id, status: 'done', cached: true } : { job_id: LIVE, status: 'queued', cached: false });
  },
  startPdf: () => Promise.reject(new Error('Mock mode (no backend): PDF upload needs the backend on :8000.')),
  get: (id) => wait(JOBS[id] ?? JOBS['example-job-1']),
  methodology: () => wait(methodology as Methodology),
  events(_id, h) {
    const a = JOBS['example-job-1'];
    const r = a.result!;
    const nodes = r.graph.nodes.filter((n) => n.type !== 'domain');
    const steps: (() => void)[] = [];
    steps.push(() => h.progress({ step: 'scrape_article', message: 'Mock mode: fetching article', pct: 5 }));
    steps.push(() => h.node(nodes.find((n) => n.type === 'article')!));
    steps.push(() => h.progress({ step: 'extract_claims', message: `Found ${r.claims.length} central claims`, pct: 20 }));
    r.claims.forEach((c) => steps.push(() => {
      h.claim({ ...c, verdict: 'not_checkable', evidence: null, explanation: 'Pending' });
      h.edge({ id: `e-a-${c.id}`, source: 'a', target: c.id, type: 'makes_claim' });
    }));
    steps.push(() => h.progress({ step: 'fetch_sources', message: 'Fetching sources', pct: 35 }));
    r.sources.forEach((s) => steps.push(() => {
      h.node(nodes.find((n) => n.id === s.id)!);
      r.graph.edges.filter((e) => e.type === 'cites' && e.target === s.id).forEach(h.edge);
    }));
    r.claims.forEach((_c, i) => steps.push(() => h.progress({ step: 'verify_claims', message: `Checking claim ${i + 1} of ${r.claims.length}`, pct: 50 + i * 8 })));
    steps.push(() => h.progress({ step: 'infra_signals', message: 'Checking analytics IDs and domain age', pct: 85 }));
    steps.push(() => h.progress({ step: 'verdict', message: 'Applying the two-signal rule', pct: 95 }));
    steps.push(() => h.done(a));
    const timers = steps.map((fn, i) => setTimeout(fn, 400 + i * 700));
    return () => timers.forEach(clearTimeout);
  },
};

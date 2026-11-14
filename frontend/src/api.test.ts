import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('bundled demo cases', () => {
  it('are listed when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new Error('down')));
    const ds = await api.demo();
    expect(ds).toHaveLength(3);
    expect(ds.every((d) => d.job_id.startsWith('example-job'))).toBe(true);
  });

  it('are listed when the backend has no precomputed cases yet', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(new Response('[]', { status: 200 })));
    expect(await api.demo()).toHaveLength(3);
  });

  it('give way to the backend list once it has cases', async () => {
    const backend = [{ key: 'real', label: 'Real', url: 'https://x.test/a', job_id: 'demo-real', expected_verdict: 'well_sourced' }];
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(JSON.stringify(backend), { status: 200 })));
    expect((await api.demo()).map((d) => d.key)).toEqual(['real']);
  });

  it('load their result without any request', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const a = await api.get('example-job-3');
    expect(a.status).toBe('done');
    expect(a.result).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

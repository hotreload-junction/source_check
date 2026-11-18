import { describe, expect, it } from 'vitest';
import { normaliseApiBase, originPattern, resultLink } from './config';
import { buildManifest } from './manifest';
import { cleanSelection, selectionProblem, unreadableReason } from './page';
import { sameDocument } from './state';

describe('manifest', () => {
  it('chrome: MV3 service worker + side panel, no standing access to every site', () => {
    const m = buildManifest('chrome', '1.2.3');
    expect(m.manifest_version).toBe(3);
    expect(m.background).toEqual({ service_worker: 'background.js', type: 'module' });
    expect(m.side_panel).toEqual({ default_path: 'sidepanel.html' });
    expect(m.permissions).toEqual(expect.arrayContaining(['sidePanel', 'activeTab', 'scripting', 'contextMenus', 'storage']));
    expect(m.host_permissions).not.toContain('<all_urls>');
    expect((m.host_permissions as string[]).every((h) => !h.includes('*.'))).toBe(true);
    expect(m).not.toHaveProperty('sidebar_action');
  });
  it('firefox: sidebar + event page, gecko id, no sidePanel permission', () => {
    const m = buildManifest('firefox', '1.2.3');
    expect(m.sidebar_action).toMatchObject({ default_panel: 'sidepanel.html' });
    expect(m.background).toEqual({ scripts: ['background.js'], type: 'module' });
    expect(m.permissions).not.toContain('sidePanel');
    expect(m).not.toHaveProperty('side_panel');
    expect(m.browser_specific_settings).toBeTruthy();
  });
});

describe('config', () => {
  it('normalises server addresses', () => {
    expect(normaliseApiBase(' https://sourcecheck.tech/ ')).toBe('https://sourcecheck.tech');
    expect(normaliseApiBase('http://localhost:8000')).toBe('http://localhost:8000');
    expect(normaliseApiBase('https://x.org/sub/')).toBe('https://x.org/sub');
    expect(normaliseApiBase('ftp://x.org')).toBeNull();
    expect(normaliseApiBase('not a url')).toBeNull();
    expect(normaliseApiBase('https://u:p@x.org')).toBeNull();
  });
  it('builds permission patterns and result links', () => {
    expect(originPattern('http://localhost:8000/api')).toBe('http://localhost/*');
    expect(originPattern('https://news.example.org/a?b=1')).toBe('https://news.example.org/*');
    expect(originPattern('chrome://extensions')).toBeNull();
    expect(resultLink('https://sourcecheck.tech', 'ab12')).toBe('https://sourcecheck.tech/#/trace?job=ab12');
  });
});

describe('page', () => {
  it('validates selections like the backend (12..1500 characters)', () => {
    expect(cleanSelection('  a\n\n b ')).toBe('a b');
    expect(selectionProblem('')).toMatch(/Select/);
    expect(selectionProblem('the minister')).toBeNull();
    expect(selectionProblem('minister')).toMatch(/too short/);
    expect(selectionProblem('x'.repeat(1501))).toMatch(/longer/);
  });
  it('refuses pages no extension can read', () => {
    expect(unreadableReason('chrome://settings')).toMatch(/browser page/);
    expect(unreadableReason('https://chromewebstore.google.com/detail/x')).toMatch(/store/);
    expect(unreadableReason('file:///tmp/a.html')).toMatch(/Local files/);
    expect(unreadableReason('https://example.org/report.pdf')).toMatch(/PDF/);
    expect(unreadableReason(undefined)).toBeTruthy();
    expect(unreadableReason('https://www.reuters.com/world/story')).toBeNull();
  });
  it('treats in-page anchors as the same document', () => {
    expect(sameDocument('https://a.org/x#top', 'https://a.org/x')).toBe(true);
    expect(sameDocument('https://a.org/x', 'https://a.org/y')).toBe(false);
    expect(sameDocument(null, 'https://a.org/x')).toBe(false);
  });
});

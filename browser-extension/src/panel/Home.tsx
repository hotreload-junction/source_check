// The panel's start view for the page in the active tab: check the whole article, or the passage selected on it.
import { useState, type CSSProperties, type ReactNode } from 'react';
import { Wordmark } from '@app/Brand';
import { CL, TH } from '@app/theme';
import { startCheck } from '../check';
import { originPattern, resultLink } from '../config';
import { unreadableReason } from '../page';
import type { CheckState } from '../state';

const hostOf = (url?: string) => { try { return new URL(url ?? '').hostname.replace(/^www\./, ''); } catch { return ''; } };

/** Reading the page from a click INSIDE the panel is not covered by activeTab: ask for this one site.
 * Must be the first await after the click (the permission prompt needs the user's gesture). */
async function allowSite(url: string | undefined): Promise<void> {
  const pattern = originPattern(url ?? '');
  if (!pattern) return;
  try {
    if (!(await chrome.permissions.contains({ origins: [pattern] }))) await chrome.permissions.request({ origins: [pattern] });
  } catch { /* declined or unsupported: activeTab may still cover the tab, startCheck explains otherwise */ }
}

const btn = (primary: boolean, disabled: boolean): CSSProperties => ({
  width: '100%', padding: '12px 16px', borderRadius: 12, fontFamily: TH.font, fontSize: 15, fontWeight: 600, cursor: disabled ? 'default' : 'pointer',
  border: `1.5px solid ${primary ? TH.ink : TH.line}`, background: primary ? TH.ink : TH.card, color: primary ? '#fff' : TH.ink,
  opacity: disabled ? 0.5 : 1, textAlign: 'center',
});
const card: CSSProperties = { background: TH.card, border: `1px solid ${TH.line}`, borderRadius: 14, padding: 14 };
const cap: CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', color: TH.mut };

function Box({ color, children }: { color: string; children: ReactNode }) {
  return <div role="status" style={{ ...card, borderColor: color, borderWidth: 1.5, fontSize: 13.5, lineHeight: 1.45 }}>{children}</div>;
}

export function Home({ tab, state, apiBase, onShow }: { tab: chrome.tabs.Tab | null; state: CheckState | null; apiBase: string; onShow: (jobId: string) => void }) {
  const [busy, setBusy] = useState(false);
  const blocked = unreadableReason(tab?.url);
  const working = busy || state?.phase === 'reading' || state?.phase === 'starting';
  const host = hostOf(tab?.url);

  const run = async (selection: boolean) => {
    if (!tab?.id || working) return;
    setBusy(true);
    try {
      await allowSite(tab.url);
      await startCheck({ tabId: tab.id, url: tab.url, title: tab.title, selection });
    } finally { setBusy(false); }
  };

  return (
    <div style={{ minHeight: '100vh', background: TH.bg, color: TH.ink, fontFamily: TH.font, fontSize: 14, padding: 16, boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 14 }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 18 }}><Wordmark /></span>
        <span style={{ flex: 1 }} />
        <a href="#/methodology" style={{ fontSize: 13, fontWeight: 600, color: TH.mut, textDecoration: 'none' }}>How it works</a>
        <button onClick={() => chrome.runtime.openOptionsPage()} title="Server settings" aria-label="Server settings"
          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 16, color: TH.mut, padding: 4 }}>⚙</button>
      </header>

      <section style={card} aria-label="Page in this tab">
        <div style={cap}>This tab</div>
        <div style={{ marginTop: 6, fontWeight: 600, fontSize: 15, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{tab?.title || 'No page'}</div>
        {host && <div style={{ marginTop: 3, fontSize: 12.5, color: TH.mut }}>{host}</div>}
      </section>

      {blocked ? (
        <Box color={TH.line}>{blocked}</Box>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          <button style={btn(true, working)} disabled={working} onClick={() => run(false)}>Check this article</button>
          <button style={btn(false, working)} disabled={working} onClick={() => run(true)}>Check the selected claim</button>
        </div>
      )}

      {state?.phase === 'reading' && <Box color={TH.line}>Reading the page…</Box>}
      {state?.phase === 'starting' && <Box color={TH.line}>{state.selection ? 'Sending the page and your selection…' : 'Sending the page…'}</Box>}
      {state?.phase === 'error' && <Box color={CL.r}><b>Not checked.</b> {state.error}</Box>}
      {state?.phase === 'started' && state.jobId && (
        <div style={{ display: 'grid', gap: 8 }}>
          <button style={btn(false, false)} onClick={() => onShow(state.jobId!)}>Show the result for this page</button>
          <a href={resultLink(apiBase, state.jobId)} target="_blank" rel="noreferrer" style={{ fontSize: 12.5, color: TH.mut, textAlign: 'center' }}>Open it on the website</a>
        </div>
      )}

      <section style={{ ...card, background: 'transparent', fontSize: 13, lineHeight: 1.5, color: TH.mut }}>
        <div style={{ ...cap, marginBottom: 6 }}>Check one claim</div>
        Select a sentence in the article, right-click it and choose <b style={{ color: TH.ink }}>Check “…” with Source Check</b>.
        That sentence is checked against its sources. The whole article is read too, and the article verdict
        still comes only from its central claims.
      </section>

      <span style={{ flex: 1 }} />
      <footer style={{ fontSize: 11.5, lineHeight: 1.5, color: TH.mut }}>
        The page as you see it (its HTML, and your selection) is sent to {hostOf(apiBase) || apiBase} only when you start a check.
        Shortcut: Alt+Shift+S.
      </footer>
    </div>
  );
}

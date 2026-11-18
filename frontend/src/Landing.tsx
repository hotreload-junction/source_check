import { useEffect, useRef, useState, type ChangeEvent, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent } from 'react';
import { api } from './api';
import { Wordmark } from './Brand';
import { useCompact, useTouch } from './responsive';
import { CL, MONO, TH } from './theme';
import type { DemoCase } from './types';

const GRAD = `linear-gradient(90deg, ${CL.r}, ${CL.o}, ${CL.y}, ${CL.g})`;
// Gradient outline on a white card: the padding box paints the fill, the border box the gradient behind the transparent border.
const gradBorder = (fill: string) => ({ border: '1.5px solid transparent', background: `linear-gradient(${fill}, ${fill}) padding-box, ${GRAD} border-box` });
const URL_RE = /(?:https?:\/\/|www\.)[^\s]+/gi;
const GLOBE = 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18';

function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

/** Pulls links out of `raw`: the first becomes the article link (unless `current` is set), later ones are dropped. */
function absorb(raw: string, current: string | null) {
  let url = current;
  let extra = false;
  const rest = raw.replace(URL_RE, (m) => {
    const clean = m.replace(/[),.;!?]+$/, '');
    const href = /^www\./i.test(clean) ? `https://${clean}` : clean;
    try { new URL(href); } catch { return m; }
    if (url) extra = true; else url = href;
    return ' ';
  }).replace(/\s+/g, ' ').trimStart();
  return { url, rest, extra };
}

const LINK = 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1';
const UPLOAD = 'M12 15V4M8 8l4-4 4 4M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4';

// Dark rounded tile with a white line icon: the same look as the link chip's globe.
function Tile({ d }: { d: string }) {
  return (
    <span aria-hidden style={{ flex: 'none', width: 40, height: 40, borderRadius: 12, background: TH.ink, display: 'grid', placeItems: 'center' }}>
      <svg viewBox="0 0 24 24" style={{ width: 21, height: 21, fill: 'none', stroke: '#fff', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round' }}><path d={d} /></svg>
    </span>
  );
}

// Square article chip: dark globe icon + text. Used for the pasted link and in the examples.
function Chip({ label, title, onRemove }: { label: string; title?: string; onRemove?: () => void }) {
  return (
    <span title={title} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '4px 12px 4px 4px', borderRadius: 12, background: '#f1f1f0', border: `1px solid ${TH.line}`, fontWeight: 500, maxWidth: '100%' }}>
      <span style={{ flex: 'none', width: 28, height: 28, borderRadius: 8, background: TH.ink, display: 'grid', placeItems: 'center' }}>
        <svg viewBox="0 0 24 24" style={{ width: 16, height: 16, fill: 'none', stroke: '#fff', strokeWidth: 1.6, strokeLinecap: 'round' }}><path d={GLOBE} /></svg>
      </span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label="Remove link" style={{ cursor: 'pointer', border: 'none', background: 'transparent', color: TH.mut, font: 'inherit', fontSize: 20, lineHeight: 1, padding: '6px 6px 6px 4px', margin: '-6px -6px -6px 0' }}>×</button>
      )}
    </span>
  );
}

export function Landing() {
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [note, setNote] = useState('');
  const [demos, setDemos] = useState<DemoCase[]>([]);
  const compact = useCompact();
  const touch = useTouch();

  useEffect(() => { api.demo().then(setDemos, () => setDemos([])); }, []);

  const take = (raw: string) => {
    const r = absorb(raw, url);
    setUrl(r.url);
    setText(r.rest);
    setNote(r.extra ? 'One link per check. The extra link was removed.' : '');
  };

  // Typing a link only turns into a chip on space; pasting does it at once.
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const pasted = e.clipboardData.getData('text');
    if (pasted.search(URL_RE) === -1) return;
    e.preventDefault();
    const s = e.currentTarget.selectionStart ?? text.length;
    const en = e.currentTarget.selectionEnd ?? s;
    take(text.slice(0, s) + pasted + text.slice(en));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && !text && url) { setUrl(null); setNote(''); }
    else if (e.key === ' ' && text.search(URL_RE) !== -1) { e.preventDefault(); take(`${text} `); }
  };

  const ready = absorb(text, url).url !== null;

  const go = (ev: FormEvent) => {
    ev.preventDefault();
    const r = absorb(text, url);
    const q = r.rest.trim();
    if (r.url) location.hash = `#/trace?url=${encodeURIComponent(r.url)}${q ? `&q=${encodeURIComponent(q)}` : ''}`;
  };

  // F9: an uploaded PDF is analysed like an article; the result opens by its job id
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [pct, setPct] = useState(0);
  const [fname, setFname] = useState('');
  const picker = useRef<HTMLInputElement>(null);
  const sendPdf = (file: File | undefined) => {
    if (!file || uploading) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') { setNote('This file is not a PDF.'); return; }
    setUploading(true); setNote(''); setPct(0); setFname(file.name);
    api.startPdf(file, setPct).then(
      (r) => { location.hash = `#/trace?job=${encodeURIComponent(r.job_id)}`; },
      (e) => { setUploading(false); setNote(String((e as Error).message || e)); },
    );
  };
  const upload = (ev: ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = '';
    sendPdf(file);
  };
  const drop = (ev: DragEvent) => { ev.preventDefault(); setDragging(false); sendPdf(ev.dataTransfer.files?.[0]); };

  return (
    <div style={{ minHeight: '100dvh', background: '#fafaf9', color: TH.ink, fontFamily: TH.font }}>
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: compact ? 'max(18px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(28px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left))' : '36px 56px 36px', minHeight: '100dvh', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 19, letterSpacing: '-.02em' }}>
          <Wordmark />
          <a href="#/about" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 600, letterSpacing: 0, color: TH.mut, textDecoration: 'none', padding: compact ? '10px 4px' : '6px 4px' }}>About us</a>
          <a href="#/methodology" style={{ marginLeft: compact ? 16 : 32, fontSize: 14, fontWeight: 600, letterSpacing: 0, color: TH.mut, textDecoration: 'none', padding: compact ? '10px 4px' : '6px 4px' }}>How it works</a>
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', paddingBottom: compact ? 0 : '6vh', paddingTop: compact ? 24 : 48 }}>
          <h1 style={{ margin: compact ? '0 0 28px' : '0 0 44px', fontSize: compact ? 'clamp(32px, 8.6vw, 84px)' : 'clamp(36px, 4.7vw, 72px)', fontWeight: 700, lineHeight: 1.05, letterSpacing: '-.045em', textAlign: 'center' }}>
            Paste something you read.
            <br />
            See what is really{' '}
            <span style={{ color: CL.g, backgroundImage: `linear-gradient(90deg, ${CL.r}, ${CL.o} 35%, ${CL.y} 65%, ${CL.g})`, WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              behind it.
            </span>
          </h1>

          <form onSubmit={go}>
            <div style={{ display: 'flex', flexDirection: compact ? 'column' : 'row', alignItems: 'stretch', gap: compact ? 8 : 0 }}>
              {/* option 1: article link */}
              <div style={{ flex: compact ? 'none' : '1 1 0', minWidth: 0, display: 'flex', flexDirection: compact ? 'column' : 'row', alignItems: compact ? 'stretch' : 'center', gap: 8, padding: compact ? 6 : 12, ...gradBorder(TH.card), borderRadius: compact ? 20 : 26 }}>
                <div style={{ flex: 1, minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, paddingLeft: compact ? 6 : 8 }}>
                  {url ? <Chip label={hostOf(url)} title={url} onRemove={() => { setUrl(null); setNote(''); }} /> : <Tile d={LINK} />}
                  <input
                    autoFocus={!touch} // on a phone the keyboard would cover the page at once
                    inputMode="url"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onPaste={onPaste}
                    onKeyDown={onKeyDown}
                    maxLength={500}
                    placeholder={url ? 'Add a question or the quote you want checked (optional)' : 'Paste an article link'}
                    aria-label={url ? 'Optional question or quote about the article' : 'Article link'}
                    style={{ flex: '1 1 160px', minWidth: 0, padding: '12px 6px', border: 'none', outline: 'none', background: 'transparent', font: 'inherit', fontSize: compact ? 17 : 20 }}
                  />
                </div>
                <button type="submit" disabled={!ready} style={{ cursor: ready ? 'pointer' : 'not-allowed', alignSelf: compact ? 'stretch' : 'flex-end', padding: compact ? '14px 20px' : '16px 26px', border: 'none', borderRadius: 14, font: 'inherit', fontWeight: 600, fontSize: compact ? 17 : 18, background: ready ? TH.ink : '#d8d8dc', color: ready ? '#fff' : '#9a9aa2' }}>
                  Check
                </button>
              </div>

              <div aria-hidden style={{ flex: 'none', alignSelf: 'center', width: compact ? 'auto' : 72, textAlign: 'center', color: TH.mut, fontFamily: MONO, fontSize: 12, fontWeight: 700, letterSpacing: '.14em' }}>OR</div>

              {/* option 2: PDF file (click or drag and drop) */}
              <div
                role="button"
                tabIndex={0}
                aria-label="Upload a PDF"
                onClick={() => !uploading && picker.current?.click()}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!uploading) picker.current?.click(); } }}
                onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={drop}
                style={{ flex: compact ? 'none' : '1 1 0', minWidth: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16, padding: compact ? '14px 14px' : '12px 20px', cursor: uploading ? 'progress' : 'pointer', ...gradBorder(dragging ? '#f1f1f0' : TH.card), borderRadius: compact ? 20 : 26 }}
              >
                <input ref={picker} type="file" accept="application/pdf,.pdf" onChange={upload} disabled={uploading} onClick={(e) => e.stopPropagation()} style={{ display: 'none' }} />
                <Tile d={UPLOAD} />
                {uploading ? (
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 14, color: TH.mut }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: TH.ink, fontWeight: 600 }}>{fname}</span>
                      <span style={{ flex: 'none' }}>{pct >= 100 ? 'Reading…' : `${pct}%`}</span>
                    </div>
                    <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} style={{ marginTop: 8, height: 6, borderRadius: 3, background: '#e4e4e8', overflow: 'hidden' }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: TH.ink, transition: 'width .15s linear' }} />
                    </div>
                  </div>
                ) : (
                  <div style={{ minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '2px 14px' }}>
                    <span style={{ fontWeight: 700, fontSize: compact ? 18 : 20, color: TH.ink }}>Upload a PDF</span>
                    <span style={{ fontSize: compact ? 15 : 17, color: TH.mut }}>or drop it here</span>
                  </div>
                )}
              </div>
            </div>
            {note && <div role="status" style={{ marginTop: 8, fontSize: 13, color: CL.o }}>{note}</div>}
          </form>

          {demos.length > 0 && (
            <div style={{ marginTop: compact ? 30 : 56, textAlign: 'center' }}>
              <div style={{ fontSize: 15, color: TH.mut, marginBottom: 14 }}>Try asking</div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: compact ? 'stretch' : 'center', gap: 12 }}>
                {demos.map((d) => (
                  <a key={d.key} href={`#/trace?demo=${encodeURIComponent(d.key)}`} title={d.url} style={{ display: 'inline-flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 10px', maxWidth: '100%', textDecoration: 'none', color: TH.ink, padding: compact ? '12px 16px' : '14px 22px', borderRadius: compact ? 18 : 22, fontSize: compact ? 16 : 18, lineHeight: 1.4, border: `1px solid ${TH.line}`, background: TH.card }}>
                    {d.question ? `Is it true that ${d.question}?` : <>Is it true what<Chip label={d.label} />says?</>}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}

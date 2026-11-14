import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import { Logo } from './Logo';
import { CL, GLY, MONO, TH } from './theme';
import type { DemoCase } from './types';

const BAR = `linear-gradient(to bottom, ${CL.r}, ${CL.o}, ${CL.y}, ${CL.g})`;
const STEPS = ['Paste an article URL', 'Every source is traced to bedrock', 'Read a score for each link'];

function hostOf(url: string): string | null {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; }
}

export function Landing() {
  const [input, setInput] = useState('');
  const [question, setQuestion] = useState('');
  const [demos, setDemos] = useState<DemoCase[]>([]);

  useEffect(() => { api.demo().then(setDemos, () => setDemos([])); }, []);

  const go = (ev: FormEvent) => {
    ev.preventDefault();
    const u = input.trim();
    const q = question.trim();
    if (u) location.hash = `#/trace?url=${encodeURIComponent(u)}${q ? `&q=${encodeURIComponent(q)}` : ''}`;
  };

  return (
    <div style={{ minHeight: '100vh', background: '#fafaf9', color: TH.ink, fontFamily: TH.font }}>
      <div aria-hidden style={{ position: 'fixed', top: 0, bottom: 0, left: 0, width: 14, background: BAR }} />
      <div style={{ maxWidth: 1000, margin: '0 auto', padding: '28px 32px 36px 56px', minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 19, letterSpacing: '-.02em' }}>
          Source Trace
          {api.mock && <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 500, padding: '3px 8px', borderRadius: 6, background: CL.y + '33', border: `1px solid ${CL.y}` }}>MOCK DATA</span>}
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', paddingBottom: '6vh' }}>
          <h1 style={{ margin: '0 0 44px', fontSize: 'clamp(40px, 7.4vw, 84px)', fontWeight: 700, lineHeight: 1.05, letterSpacing: '-.045em' }}>
            Paste something you read.
            <br />
            See what is{' '}
            <span style={{ color: CL.g, backgroundImage: `linear-gradient(90deg, ${CL.r}, ${CL.o} 35%, ${CL.y} 65%, ${CL.g})`, WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              behind it.
            </span>
          </h1>

          <form onSubmit={go} style={{ maxWidth: 740 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 8, background: TH.card, border: `1.5px solid ${TH.ink}`, borderRadius: 22 }}>
              <input
                autoFocus
                required
                type="url"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="A link to an article"
                aria-label="Article URL"
                style={{ flex: 1, minWidth: 0, padding: '14px 14px', border: 'none', outline: 'none', background: 'transparent', font: 'inherit', fontSize: 20 }}
              />
              <button type="submit" style={{ cursor: 'pointer', padding: '16px 26px', border: 'none', borderRadius: 14, font: 'inherit', fontWeight: 600, fontSize: 18, background: TH.ink, color: '#fff' }}>
                Check
              </button>
            </div>
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={500}
              placeholder="Optional: what are you unsure about? e.g. Is the 40% figure real?"
              aria-label="Optional question about the article"
              title="Leave empty to check the whole article. With a question we also check the sentence it is about; the article verdict stays the same."
              style={{ width: '100%', marginTop: 10, padding: '11px 16px', borderRadius: 14, border: `1.5px solid ${TH.line}`, background: 'transparent', font: 'inherit', fontSize: 14 }}
            />
          </form>

          {demos.length > 0 && (
            <div style={{ marginTop: 22, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 15, color: TH.mut }}>Try</span>
              {demos.map((d) => (
                <a key={d.key} href={`#/trace?demo=${encodeURIComponent(d.key)}`} title={d.url} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, textDecoration: 'none', color: TH.ink, padding: '8px 16px 8px 12px', borderRadius: 99, fontSize: 15, fontWeight: 500, whiteSpace: 'nowrap', border: `1.5px solid ${TH.line}`, background: TH.card }}>
                  <Logo domain={hostOf(d.url)} glyph={GLY.link} color={TH.mut} size={16} />
                  {d.label}
                </a>
              ))}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: '12px 40px', flexWrap: 'wrap', paddingTop: 22, borderTop: `1px solid ${TH.line}`, fontSize: 16, color: TH.mut }}>
          {STEPS.map((s, i) => (
            <span key={s}>
              <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 13, color: TH.ink, marginRight: 8 }}>{`0${i + 1}`}</span>
              {s}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

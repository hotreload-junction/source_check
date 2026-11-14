import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import { CL, MONO, TH } from './theme';
import type { DemoCase } from './types';

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
    <div style={{ minHeight: '100vh', background: TH.bg, color: TH.ink, fontFamily: TH.font, backgroundImage: `radial-gradient(${TH.dot} ${TH.dr}px, transparent ${TH.dr}px)`, backgroundSize: `${TH.ds}px ${TH.ds}px` }}>
      <div style={{ maxWidth: 880, margin: '0 auto', padding: '28px 20px 64px', minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 17, letterSpacing: '-.01em' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, width: 20 }}>
            <div style={{ height: 4, background: TH.ink, borderRadius: 1, opacity: 0.35 }} />
            <div style={{ height: 4, background: TH.ink, borderRadius: 1, opacity: 0.65, width: '80%' }} />
            <div style={{ height: 4, background: TH.ink, borderRadius: 1, width: '60%' }} />
          </div>
          Source Trace
          {api.mock && <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 500, padding: '3px 8px', borderRadius: 6, background: CL.y + '33', border: `1px solid ${CL.y}` }}>MOCK DATA</span>}
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', paddingBottom: '12vh' }}>

        <form onSubmit={go} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', maxWidth: 720 }}>
          <input
            autoFocus
            required
            type="url"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="https://example.com/news/article"
            aria-label="Article URL"
            style={{ flex: '1 1 320px', minWidth: 0, padding: '14px 16px', borderRadius: 12, border: `${TH.bw / 2}px solid ${TH.ink}`, background: TH.card, font: 'inherit', fontSize: 16, boxShadow: TH.sh }}
          />
          <button type="submit" style={{ cursor: 'pointer', padding: '14px 24px', border: `${TH.bw / 2}px solid ${TH.ink}`, borderRadius: 12, font: 'inherit', fontWeight: 700, fontSize: 16, background: TH.ink, color: TH.bg }}>
            Trace sources
          </button>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
            placeholder="Optional: what are you unsure about? e.g. Is the 40% figure real?"
            aria-label="Optional question about the article"
            style={{ flex: '1 1 100%', minWidth: 0, padding: '11px 16px', borderRadius: 12, border: `1.5px solid ${TH.line}`, background: TH.card, font: 'inherit', fontSize: 14 }}
          />
          <div style={{ flex: '1 1 100%', fontSize: 12, color: TH.mut, lineHeight: 1.4 }}>
            Leave it empty to check the whole article. With a question we find the sentence it is about and check it against its sources as well; the article verdict stays the same.
          </div>
        </form>

        {demos.length > 0 && (
          <div style={{ marginTop: 18, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: TH.mut }}>No article at hand? Try a prepared case:</span>
            {demos.map((d) => (
              <a key={d.key} href={`#/trace?demo=${encodeURIComponent(d.key)}`} style={{ textDecoration: 'none', color: TH.ink, padding: '6px 13px', borderRadius: 99, fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', border: `1.5px solid ${TH.ink}`, background: TH.card }}>
                {d.label}
              </a>
            ))}
          </div>
        )}

        </div>
      </div>
    </div>
  );
}

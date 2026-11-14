import { useEffect, useState } from 'react';

// Site favicon, falls back to a line glyph when offline or the icon fails (demo must work with APIs down).
export function Logo({ domain, glyph, color, size }: { domain?: string | null; glyph: string; color: string; size: number }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [domain]);
  return (
    <div style={{ flex: 'none', width: size, height: size, display: 'grid', placeItems: 'center' }}>
      {domain && !bad ? (
        <img
          src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=128`}
          alt=""
          draggable={false}
          onError={() => setBad(true)}
          style={{ width: '100%', height: '100%', objectFit: 'contain' }}
        />
      ) : (
        <svg viewBox="0 0 24 24" style={{ width: '100%', height: '100%', fill: 'none', stroke: color, strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' }}>
          <path d={glyph} />
        </svg>
      )}
    </div>
  );
}

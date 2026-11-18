import { useEffect, useState } from 'react';

// Inline styles cannot hold media queries, so layout switches go through matchMedia.
// "compact" = phones and tablets in portrait: stacked header, sheets instead of side panel, page scrolls.
export const COMPACT_Q = '(max-width: 900px)';
const TOUCH_Q = '(pointer: coarse)';

const list = (q: string) => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(q) : null);

export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => list(query)?.matches ?? false);
  useEffect(() => {
    const l = list(query);
    if (!l) return;
    const h = () => setOn(l.matches);
    h();
    l.addEventListener('change', h);
    return () => l.removeEventListener('change', h);
  }, [query]);
  return on;
}

export const useCompact = () => useMedia(COMPACT_Q);
export const useTouch = () => useMedia(TOUCH_Q);
/** Viewport size in CSS px, kept current on resize and rotation (the on-screen keyboard changes the height, hence only the width is used for layout switches). */
export function useViewport(): [number, number] {
  const read = (): [number, number] => (typeof window === 'undefined' ? [1280, 800] : [window.innerWidth, window.innerHeight]);
  const [v, setV] = useState(read);
  useEffect(() => {
    const h = () => setV((o) => { const n = read(); return o[0] === n[0] && o[1] === n[1] ? o : n; });
    window.addEventListener('resize', h);
    window.addEventListener('orientationchange', h);
    return () => { window.removeEventListener('resize', h); window.removeEventListener('orientationchange', h); };
  }, []);
  return v;
}
export const isTouch = () => list(TOUCH_Q)?.matches ?? false;

// "narrow" = phones in portrait and the browser extension's side panel: the graph switches to the vertical outline
// layout (vlayout.ts), because the wide desktop tree would have to shrink to ~20 % to fit.
export const NARROW_Q = '(max-width: 640px)';
export const useNarrow = () => useMedia(NARROW_Q);

// Vertical graph layout for narrow screens (phones, the browser extension's side panel).
// The desktop layout is a wide tree (claims side by side, sources below them): fitted into ~400 px it shrinks to
// ~20 %. Here the same tree is an indented outline instead: document on top, each claim below the previous one, its
// sources indented under it. The graph is as wide as the screen at ~100 % and grows downwards (scroll to read).
import type { TEdge, TNode, Tree } from './tree';

export type Box = [number, number, number, number]; // x0, y0, x1, y1 in graph coordinates

export const VL = {
  ML: 10,       // left margin
  GW: 380,      // width of the document card; deeper levels get narrower by IND per level
  IND: 26,      // indentation per level (room for the connector line)
  GAP: 30,      // between two cards (the verdict pill sits on the lower card's top edge)
  SEC: 16,      // extra space above each claim (one section per claim)
  TOP: 26,
  BOT: 70,      // room below the last card for the zoom buttons
  MR_EXTRA: 46, // right margin when a curved "extra" edge (shared source, citation circle) needs room
};

/** Card height by kind. Fixed, so the layout never depends on rendering. */
export function vHeight(n: TNode, o: { labels: boolean; question: boolean }): number {
  if (n.kind === 'root') return o.question ? 150 : 112;
  if (n.kind === 'claim') return 120;
  if (n.kind === 'nosrc') return 52;
  return o.labels ? 104 : 80;
}

/** Document order of the outline: root, then each claim followed by its subtree (depth first). */
export function outline(tree: Tree): TNode[] {
  const out: TNode[] = [];
  const walk = (n: TNode) => { out.push(n); n.kids.forEach(walk); };
  walk(tree.root);
  return out;
}

export const hasSideEdges = (edges: TEdge[]) => edges.some((e) => e.extra || e.type === 'back');

export function verticalLayout(tree: Tree, o: { labels: (n: TNode) => boolean; question: boolean }): { box: Map<string, Box>; w: number; h: number } {
  const box = new Map<string, Box>();
  let y = VL.TOP;
  outline(tree).forEach((n, i) => {
    if (n.kind === 'claim' && i > 1) y += VL.SEC;
    const d = Math.min(n.d, 6);
    const x0 = VL.ML + d * VL.IND;
    const h = vHeight(n, { labels: o.labels(n), question: o.question });
    box.set(n.id, [x0, y, VL.ML + VL.GW, y + h]);
    y += h + VL.GAP;
  });
  const w = VL.ML + VL.GW + (hasSideEdges(tree.edges) ? VL.MR_EXTRA : VL.ML);
  return { box, w, h: y - VL.GAP + VL.BOT };
}

/** Trunk x of a parent: the vertical line its children hang off (inside the indent of the next level). */
export const trunkX = (b: Box) => b[0] + VL.IND / 2;

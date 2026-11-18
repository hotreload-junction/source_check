import { describe, expect, it } from 'vitest';
import done from '../../contracts/examples/analysis_done.json';
import { buildTree, fromResult } from './tree';
import type { Analysis } from './types';
import { VL, outline, verticalLayout } from './vlayout';

const tree = buildTree(fromResult((done as unknown as Analysis).result!), new Map());
const lay = verticalLayout(tree, { labels: () => false, question: false });

describe('verticalLayout', () => {
  it('lists the document, then each claim followed by its own sources', () => {
    const ids = outline(tree).map((n) => n.id);
    expect(ids[0]).toBe(tree.root.id);
    expect(ids.indexOf('c1')).toBeLessThan(ids.indexOf('s1'));
    expect(ids.indexOf('s1')).toBeLessThan(ids.indexOf('c2'));
    expect(ids.indexOf('nosrc:c4')).toBe(ids.indexOf('c4') + 1);
  });

  it('stacks cards top to bottom without overlap and indents by depth', () => {
    const boxes = outline(tree).map((n) => lay.box.get(n.id)!);
    boxes.slice(1).forEach((b, i) => expect(b[1]).toBeGreaterThanOrEqual(boxes[i][3] + VL.GAP));
    expect(lay.box.get('c1')![0]).toBeGreaterThan(lay.box.get(tree.root.id)![0]);
    expect(lay.box.get('s1')![0]).toBeGreaterThan(lay.box.get('c1')![0]);
    expect(lay.h).toBeGreaterThan(boxes[boxes.length - 1][3]);
  });

  it('is about one phone screen wide, so it shows near 100 % zoom', () => {
    expect(lay.w).toBeLessThanOrEqual(VL.ML + VL.GW + VL.MR_EXTRA);
    expect(lay.w).toBeGreaterThanOrEqual(VL.ML * 2 + VL.GW);
    expect(Math.max(...[...lay.box.values()].map((b) => b[2]))).toBeLessThanOrEqual(lay.w);
  });
});

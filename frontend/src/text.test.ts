import { describe, expect, it } from 'vitest';
import { locate, plain, segments } from './text';

describe('locate', () => {
  it('converts code-point offsets to UTF-16 when text has astral characters', () => {
    const text = '😀 intro. The claim here. Tail';
    const cpStart = [...text].indexOf('T');
    const span = locate(text, cpStart, cpStart + 'The claim here.'.length, 'The claim here.');
    expect(span).not.toBeNull();
    expect(text.slice(span![0], span![1])).toBe('The claim here.');
  });

  it('falls back to searching the claim text when offsets do not match', () => {
    expect(locate('abc The claim', 0, 3, 'The claim')).toEqual([4, 13]);
    expect(locate('abc', null, null, 'missing')).toBeNull();
  });
});

describe('segments', () => {
  it('splits text around marks and strips markdown links', () => {
    const text = 'A [link](http://x) B claim C';
    const s = text.indexOf('claim');
    const segs = segments(text, [{ start: s, end: s + 5, c: 'red', id: 'c1' }]);
    expect(segs.map((g) => g.t).join('')).toBe('A link B claim C');
    expect(segs.find((g) => g.id === 'c1')!.t).toBe('claim');
  });

  it('drops heading markers', () => {
    expect(plain('# Title\n\nBody')).toBe('Title\n\nBody');
  });
});

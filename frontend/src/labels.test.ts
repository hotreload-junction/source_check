import { describe, expect, it } from 'vitest';
import { archiveText, cardLabels, reregisteredYears, rspLines, typeWord } from './labels';
import type { Domain, RspEntry, Source } from './types';

const source = (extra: Partial<Source> = {}): Source => ({
  id: 's1', url: null, domain: 'x.example', kind: 'link', attribution_text: null, depth: 1, parent_source_id: null, fetch_status: 'ok', fetched_via: null, ...extra,
});
const domain = (extra: Partial<Domain> = {}): Domain => ({
  domain: 'x.example', created_at: null, age_days: null, analytics_ids: [], adsense_ids: [], cluster_id: null, is_article_domain: false, ...extra,
});
const rsp = (statuses: RspEntry['statuses'], extra: Partial<RspEntry> = {}): RspEntry => ({ source: 'X', statuses, blacklisted: false, last_discussed: null, snapshot: '2026-11-14', ...extra });

describe('source labels (Q1/Q2)', () => {
  it('names the source type, never an unknown one', () => {
    expect(typeWord(source({ source_type: 'press_release' }))).toBe('Press release');
    expect(typeWord(source({ source_type: 'unknown' }))).toBeNull();
    expect(typeWord(source())).toBeNull(); // older results have no source_type
  });

  it('folds Wikipedia Perennial Sources rows into one card label', () => {
    expect(cardLabels(domain({ wikipedia_rsp: [rsp(['generally_reliable'])] }))[0]).toMatchObject({ text: 'Wikipedia: reliable', tone: 'good' });
    expect(cardLabels(domain({ wikipedia_rsp: [rsp(['generally_unreliable'], { blacklisted: true })] }))[0]).toMatchObject({ text: 'Wikipedia: deprecated', tone: 'bad' });
    expect(cardLabels(domain({ wikipedia_rsp: [rsp(['no_consensus', 'generally_reliable'])] }))[0]).toMatchObject({ text: 'Wikipedia: mixed', tone: 'warn' });
    expect(rspLines([rsp(['deprecated'], { source: 'RT', blacklisted: true, last_discussed: 2022 })])).toEqual(['“RT”: deprecated, on the spam blacklist, last discussed 2022']);
  });

  it('no data, no label: unlisted domains and missing domains stay unlabelled', () => {
    expect(cardLabels(domain())).toEqual([]);
    expect(cardLabels(undefined)).toEqual([]);
  });

  it('flags re-registration only when archived pages predate the registration by a year or more', () => {
    expect(reregisteredYears(domain({ created_at: '2026-09-20T00:00:00Z', first_archived_at: '2014-03-02' }))).toBe(12);
    expect(reregisteredYears(domain({ created_at: '2010-01-01T00:00:00Z', first_archived_at: '2010-06-01' }))).toBeNull();
    expect(reregisteredYears(domain({ created_at: '2026-09-20T00:00:00Z', first_archived_at: '2026-01-01' }))).toBeNull(); // < 365 days
    expect(reregisteredYears(domain({ first_archived_at: '2014-03-02' }))).toBeNull(); // no RDAP date
    expect(cardLabels(domain({ created_at: '2026-09-20T00:00:00Z', first_archived_at: '2014-03-02' })).map((l) => l.key)).toEqual(['rereg']);
  });

  it('tells "never archived" apart from "not checked"', () => {
    expect(archiveText(domain({ first_archived_at: '2001-01-01' }))).toBe('First captured 2001-01-01');
    expect(archiveText(domain({ reputation_notes: ['Never captured by the Wayback Machine'] }))).toBe('Never captured by the Wayback Machine');
    expect(archiveText(domain())).toBeNull(); // lookup off or failed: unknown is neutral
  });
});

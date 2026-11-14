// Per-source labels from PLAN Q1 (source type) and Q2 (reputation snapshots). Context for the reader only:
// they never change a claim or article verdict (DECISIONS "SOURCE QUALITY"). Every label restates a backend
// fact; nothing here is guessed. Missing data = no label.

import { rspVerdict, TYPE_WORD, type RspVerdict } from './trust';
import type { Domain, RspEntry, Source } from './types';

export type Tone = 'good' | 'warn' | 'bad';
export interface Label { key: string; text: string; tone: Tone; title: string }

// Same rule as backend/app/signals/reputation.py REREGISTERED_MIN_DAYS: a capture this long before the
// registration date cannot be clock noise.
const REREGISTERED_MIN_DAYS = 365;

const STATUS_TEXT: Record<RspEntry['statuses'][number], string> = {
  generally_reliable: 'generally reliable', no_consensus: 'no consensus', generally_unreliable: 'generally unreliable', deprecated: 'deprecated',
};
const RSP_LABEL: Record<RspVerdict, [string, Tone]> = {
  reliable: ['Wikipedia: reliable', 'good'], mixed: ['Wikipedia: mixed', 'warn'],
  unreliable: ['Wikipedia: unreliable', 'bad'], deprecated: ['Wikipedia: deprecated', 'bad'],
};

/** "Press release", "Official", ... ; null when the type is unknown or absent (older results). */
export function typeWord(s: Source): string | null {
  const t = s.source_type;
  if (!t || t === 'unknown') return null;
  const w = TYPE_WORD[t];
  return w[0].toUpperCase() + w.slice(1);
}

/** One line per WP:RSP row, e.g. `"RT": deprecated, on the spam blacklist, last discussed 2022`. */
export function rspLines(rows: RspEntry[]): string[] {
  return rows.map((r) =>
    `“${r.source}”: ${r.statuses.map((x) => STATUS_TEXT[x]).join(' / ')}${r.blacklisted ? ', on the spam blacklist' : ''}${r.last_discussed ? `, last discussed ${r.last_discussed}` : ''}`);
}

/** Whole years the first Wayback capture predates the current registration; null = no sign of re-registration. */
export function reregisteredYears(d: Domain | undefined): number | null {
  if (!d?.first_archived_at || !d.created_at) return null;
  const a = Date.parse(d.first_archived_at.slice(0, 10) + 'T00:00:00Z'), c = Date.parse(d.created_at);
  if (Number.isNaN(a) || Number.isNaN(c)) return null;
  const days = Math.floor((c - a) / 86_400_000);
  return days >= REREGISTERED_MIN_DAYS ? Math.floor(days / 365) : null;
}

/** Wayback answer for the panel: a date, "never captured" (a real answer), or null = not checked / lookup failed. */
export function archiveText(d: Domain | undefined): string | null {
  if (d?.first_archived_at) return `First captured ${d.first_archived_at}`;
  return d?.reputation_notes?.some((x) => x.startsWith('Never captured')) ? 'Never captured by the Wayback Machine' : null;
}

/** Labels shown on a source card (at most two: reputation list, re-registration). */
export function cardLabels(d: Domain | undefined): Label[] {
  const out: Label[] = [];
  const rows = d?.wikipedia_rsp ?? [];
  const r = rspVerdict(rows);
  if (r) {
    const [text, tone] = RSP_LABEL[r];
    out.push({ key: 'rsp', text, tone, title: `Wikipedia Perennial Sources (snapshot ${rows[0].snapshot}): ${rspLines(rows).join('; ')}` });
  }
  const yrs = reregisteredYears(d);
  if (yrs != null) {
    out.push({ key: 'rereg', text: 'Re-registered', tone: 'warn',
      title: `Archived pages from ${d!.first_archived_at} predate the current registration (${d!.created_at!.slice(0, 10)}) by ${yrs}+ year(s): the domain existed before and was registered again.` });
  }
  return out;
}

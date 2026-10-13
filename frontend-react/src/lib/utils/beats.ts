import type { BeatSample } from '@/lib/api/services/beat-capture.service';

const CSV_COLUMNS = [
  'timestamp',
  'zone_offset',
  'interval_ms',
  'beat_series_id',
  'preceded_by_gap',
  'provider',
  'source',
  'device',
] as const;

function csvField(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/**
 * One row per beat, as stored: the beat's own time and the interval since the
 * previous beat in its series. Gap-spanning intervals are kept and flagged, so
 * every beat time stays recoverable; leave them out of interval statistics.
 */
export function beatsToCsv(beats: BeatSample[]): string {
  const rows = beats.map((beat) =>
    [
      beat.timestamp,
      beat.zone_offset,
      beat.value,
      beat.beat_series_id,
      beat.preceded_by_gap,
      beat.source?.provider,
      beat.source?.source,
      beat.source?.device,
    ]
      .map(csvField)
      .join(',')
  );
  return [CSV_COLUMNS.join(','), ...rows].join('\n') + '\n';
}

/**
 * A `datetime-local` input's value (the browser's zone, no offset) as an
 * absolute ISO instant, or null when it is empty or not a date.
 */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** `beats-<user prefix>-<UTC start, compact>.csv`. */
export function beatsFilename(userId: string, startIso: string): string {
  const stamp = startIso.replace(/[-:]/g, '').replace(/\.\d+/, '').slice(0, 15);
  return `beats-${userId.slice(0, 8)}-${stamp}Z.csv`;
}

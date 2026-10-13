import { describe, expect, it } from 'vitest';
import type { BeatSample } from '@/lib/api/services/beat-capture.service';
import { beatsFilename, beatsToCsv, localInputToIso } from './beats';

const beat = (overrides: Partial<BeatSample> = {}): BeatSample => ({
  timestamp: '2026-10-12T03:00:00.850000Z',
  zone_offset: '-04:00',
  value: 850,
  unit: 'ms',
  source: { provider: 'apple', source: 'Apple Watch', device: 'Watch7,12' },
  beat_series_id: 'S1',
  preceded_by_gap: false,
  ...overrides,
});

describe('beatsToCsv', () => {
  it('writes one row per beat with its series and gap flag', () => {
    const csv = beatsToCsv([
      beat(),
      beat({
        timestamp: '2026-10-12T03:00:07.350000Z',
        value: 6500,
        preceded_by_gap: true,
      }),
    ]);

    expect(csv.split('\n')).toEqual([
      'timestamp,zone_offset,interval_ms,beat_series_id,preceded_by_gap,provider,source,device',
      '2026-10-12T03:00:00.850000Z,-04:00,850,S1,false,apple,Apple Watch,"Watch7,12"',
      '2026-10-12T03:00:07.350000Z,-04:00,6500,S1,true,apple,Apple Watch,"Watch7,12"',
      '',
    ]);
  });

  it('leaves unknown fields empty rather than writing a value', () => {
    const csv = beatsToCsv([
      beat({ zone_offset: null, source: null, preceded_by_gap: null }),
    ]);

    expect(csv.split('\n')[1]).toBe('2026-10-12T03:00:00.850000Z,,850,S1,,,,');
  });

  it('quotes fields holding quotes', () => {
    const csv = beatsToCsv([
      beat({
        source: { provider: 'apple', source: 'My "Watch"', device: null },
      }),
    ]);

    expect(csv.split('\n')[1]).toContain(',"My ""Watch""",');
  });
});

describe('localInputToIso', () => {
  it('reads the input in the browser zone and returns an instant', () => {
    const iso = localInputToIso('2026-10-12T22:30');

    expect(iso).toBe(new Date(2026, 9, 12, 22, 30).toISOString());
  });

  it('returns null for an empty or invalid value', () => {
    expect(localInputToIso('')).toBeNull();
    expect(localInputToIso('not a date')).toBeNull();
  });
});

describe('beatsFilename', () => {
  it('names the user and the UTC start', () => {
    expect(
      beatsFilename(
        '3f2a9c1e-0000-4000-8000-000000000000',
        '2026-10-12T02:00:00.000Z'
      )
    ).toBe('beats-3f2a9c1e-20261012T020000Z.csv');
  });
});

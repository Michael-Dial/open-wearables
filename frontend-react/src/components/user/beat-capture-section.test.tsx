// @vitest-environment node
import { describe, it, expect, vi } from 'vitest';
import { renderToString } from 'react-dom/server';

const mutation = { mutate: vi.fn(), isPending: false, variables: undefined };

vi.mock('@/hooks/api/use-beat-capture', () => ({
  useBeatCaptureSetting: () => ({
    isLoading: false,
    data: {
      user_id: 'user-1',
      mode: 'sleep',
      lookback_days: 7,
      sleep_padding_minutes: 30,
      is_default: true,
      updated_at: null,
    },
  }),
  useBeatCaptureWindows: () => ({
    isLoading: false,
    data: [
      {
        id: 'w1',
        user_id: 'user-1',
        start_at: '2026-10-12T02:00:00Z',
        end_at: '2026-10-12T11:00:00Z',
        status: 'fulfilled',
        note: 'PSG night 3',
        created_at: '2026-10-11T12:00:00Z',
        fulfilled_at: '2026-10-12T12:00:00Z',
        beats_reported: 5400,
        series_reported: 90,
      },
      {
        id: 'w2',
        user_id: 'user-1',
        start_at: '2026-10-13T02:00:00Z',
        end_at: '2026-10-13T11:00:00Z',
        status: 'pending',
        note: null,
        created_at: '2026-10-12T12:00:00Z',
        fulfilled_at: null,
        beats_reported: null,
        series_reported: null,
      },
    ],
  }),
  useUpdateBeatCaptureSetting: () => mutation,
  useCreateBeatCaptureWindow: () => mutation,
  useCancelBeatCaptureWindow: () => mutation,
}));

const { BeatCaptureSection } = await import('./beat-capture-section');

describe('BeatCaptureSection', () => {
  // The user page renders on the server first, where there is no window.
  const html = renderToString(<BeatCaptureSection userId="user-1" />);

  it('says when the instance default applies', () => {
    expect(html).toContain('Instance default, not set for this user');
  });

  it('marks the stored mode', () => {
    expect(html).toMatch(/aria-pressed="true"[^>]*>.*?During sleep/);
  });

  it('shows what the phone reported for a fulfilled request', () => {
    expect(html).toContain('PSG night 3');
    expect(html).toContain('5400 beats · 90 series');
  });

  it('offers cancel only on a pending request', () => {
    expect(html.match(/title="Cancel request"/g)).toHaveLength(1);
    expect(html.match(/title="Download stored beats \(CSV\)"/g)).toHaveLength(
      2
    );
  });
});

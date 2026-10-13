import { apiClient } from '../client';
import { API_ENDPOINTS } from '../config';
import { ApiError } from '../../errors/api-error';
import type { PaginatedResponse } from '../types';

export type BeatCaptureMode = 'off' | 'sleep' | 'all_day';
export type BeatCaptureWindowStatus = 'pending' | 'fulfilled' | 'cancelled';

export interface BeatCaptureSetting {
  user_id: string;
  mode: BeatCaptureMode;
  lookback_days: number;
  sleep_padding_minutes: number;
  /** True when nothing is stored for this user and the instance default applies. */
  is_default: boolean;
  updated_at: string | null;
}

export interface BeatCaptureSettingUpdate {
  mode?: BeatCaptureMode;
  lookback_days?: number;
  sleep_padding_minutes?: number;
}

export interface BeatCaptureWindow {
  id: string;
  user_id: string;
  start_at: string;
  end_at: string;
  status: BeatCaptureWindowStatus;
  note: string | null;
  created_at: string;
  fulfilled_at: string | null;
  beats_reported: number | null;
  series_reported: number | null;
}

export interface BeatCaptureWindowCreate {
  start_at: string;
  end_at: string;
  note?: string;
}

/** One beat as the timeseries endpoint returns it for pulse_to_pulse_interval. */
export interface BeatSample {
  timestamp: string;
  zone_offset: string | null;
  value: number;
  unit: string;
  source: {
    provider: string;
    source: string | null;
    device: string | null;
  } | null;
  beat_series_id: string | null;
  preceded_by_gap: boolean | null;
}

const BEAT_SERIES_TYPE = 'pulse_to_pulse_interval';
const PAGE_SIZE = 1000;

async function call<T>(request: () => Promise<T>): Promise<T> {
  try {
    return await request();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw ApiError.networkError((error as Error).message);
  }
}

export const beatCaptureService = {
  getSetting(userId: string): Promise<BeatCaptureSetting> {
    return call(() =>
      apiClient.get<BeatCaptureSetting>(API_ENDPOINTS.userBeatCapture(userId))
    );
  },

  updateSetting(
    userId: string,
    data: BeatCaptureSettingUpdate
  ): Promise<BeatCaptureSetting> {
    return call(() =>
      apiClient.put<BeatCaptureSetting>(
        API_ENDPOINTS.userBeatCapture(userId),
        data
      )
    );
  },

  listWindows(userId: string): Promise<BeatCaptureWindow[]> {
    return call(() =>
      apiClient.get<BeatCaptureWindow[]>(
        API_ENDPOINTS.userBeatCaptureWindows(userId)
      )
    );
  },

  createWindow(
    userId: string,
    data: BeatCaptureWindowCreate
  ): Promise<BeatCaptureWindow> {
    return call(() =>
      apiClient.post<BeatCaptureWindow>(
        API_ENDPOINTS.userBeatCaptureWindows(userId),
        data
      )
    );
  },

  cancelWindow(userId: string, windowId: string): Promise<BeatCaptureWindow> {
    return call(() =>
      apiClient.delete<BeatCaptureWindow>(
        API_ENDPOINTS.userBeatCaptureWindow(userId, windowId)
      )
    );
  },

  /** Every beat in [start, end), following the cursor to the last page. */
  async fetchBeats(
    userId: string,
    start: string,
    end: string
  ): Promise<BeatSample[]> {
    const beats: BeatSample[] = [];
    let cursor: string | undefined;
    do {
      const page = await call(() =>
        apiClient.get<PaginatedResponse<BeatSample>>(
          `/api/v1/users/${userId}/timeseries`,
          {
            params: {
              start_time: start,
              end_time: end,
              types: [BEAT_SERIES_TYPE],
              limit: PAGE_SIZE,
              cursor,
            },
          }
        )
      );
      beats.push(...page.data);
      cursor = page.pagination.has_more
        ? (page.pagination.next_cursor ?? undefined)
        : undefined;
    } while (cursor);
    return beats;
  },
};

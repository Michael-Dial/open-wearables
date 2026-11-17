import { apiClient } from '@/lib/api/client';
import { API_ENDPOINTS } from '@/lib/api/config';

/**
 * Shareable pairing links carry a connect token (`?t=`): the pair page is public,
 * and the backend's authorize endpoint only starts a provider sign-in for a caller
 * that proves which user it speaks for. The token is good for that one user only.
 */
export const PAIR_TOKEN_PARAM = 't';

const STORAGE_KEY = (userId: string) => `pair_token:${userId}`;

interface ConnectTokenResponse {
  token: string;
  expires_at: string;
  pair_url: string;
}

export function pairingLink(
  origin: string,
  userId: string,
  token: string
): string {
  const query = new URLSearchParams({ [PAIR_TOKEN_PARAM]: token });
  return `${origin.replace(/\/+$/, '')}/users/${userId}/pair?${query}`;
}

/** Mints a connect token as the signed-in developer and builds the link on this origin. */
export async function fetchPairingLink(userId: string): Promise<string> {
  const { token } = await apiClient.post<ConnectTokenResponse>(
    API_ENDPOINTS.userConnectToken(userId)
  );
  return pairingLink(window.location.origin, userId, token);
}

/** Keeps the link's token for the tab, so the success page can offer "connect another". */
export function rememberPairToken(userId: string, token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY(userId), token);
  } catch {
    // Storage blocked: the success page just won't offer a way back.
  }
}

export function rememberedPairToken(userId: string): string | undefined {
  try {
    return sessionStorage.getItem(STORAGE_KEY(userId)) ?? undefined;
  } catch {
    return undefined;
  }
}

import { apiClient } from '../client';
import { API_ENDPOINTS } from '../config';
import type {
  Provider,
  ProviderAppSettings,
  ProviderAppSettingsUpdate,
} from '../types';

/**
 * Update payload for OAuth provider settings.
 * Maps provider identifiers to their enabled/disabled state.
 */
export interface OAuthProvidersUpdate {
  /** Record of provider identifiers to their enabled status (true = enabled, false = disabled) */
  providers: Record<string, boolean>;
}

/**
 * Service for managing OAuth provider configurations.
 */
export const oauthService = {
  /**
   * Retrieves a list of OAuth providers.
   *
   * @param cloudOnly - If true, only returns cloud-based providers. Defaults to false.
   * @param enabledOnly - If true, only returns enabled providers. Defaults to false.
   * @returns Promise resolving to an array of Provider objects.
   */
  async getProviders(
    cloudOnly: boolean = false,
    enabledOnly: boolean = false
  ): Promise<Provider[]> {
    const params = new URLSearchParams();
    if (cloudOnly) params.append('cloud_only', 'true');
    if (enabledOnly) params.append('enabled_only', 'true');

    const endpoint = params.toString()
      ? `${API_ENDPOINTS.oauthProviders}?${params}`
      : API_ENDPOINTS.oauthProviders;

    return apiClient.get<Provider[]>(endpoint);
  },

  /**
   * Updates the enabled/disabled state of OAuth providers.
   *
   * @param data - Update payload containing provider states.
   * @returns Promise resolving to an object containing the updated list of providers.
   */
  async updateProviders(
    data: OAuthProvidersUpdate
  ): Promise<{ providers: Provider[] }> {
    return apiClient.put<{ providers: Provider[] }>(
      API_ENDPOINTS.oauthProviders,
      data
    );
  },

  /**
   * Updates a single provider setting (is_enabled and/or live_sync_mode).
   */
  async updateProviderSetting(
    provider: string,
    update: { is_enabled?: boolean; live_sync_mode?: 'pull' | 'webhook' }
  ): Promise<Provider> {
    return apiClient.put<Provider>(
      API_ENDPOINTS.providerSetting(provider),
      update
    );
  },

  /**
   * App registration settings of a provider (client ID, redirect URI, scopes,
   * webhook tokens) and where each value in use comes from. Secrets are never
   * returned, only whether they are set.
   */
  async getProviderCredentials(provider: string): Promise<ProviderAppSettings> {
    return apiClient.get<ProviderAppSettings>(
      API_ENDPOINTS.providerCredentials(provider)
    );
  },

  /**
   * Saves app registration settings, overriding the backend environment.
   * A null value removes the saved value so the environment applies again.
   */
  async updateProviderCredentials(
    provider: string,
    update: ProviderAppSettingsUpdate
  ): Promise<ProviderAppSettings> {
    return apiClient.put<ProviderAppSettings>(
      API_ENDPOINTS.providerCredentials(provider),
      update
    );
  },
};

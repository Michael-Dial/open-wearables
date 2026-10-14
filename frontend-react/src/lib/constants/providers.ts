/**
 * Providers that are an extra grant on another provider's account rather than a
 * device of their own, mapped to the provider whose connection asks for them.
 *
 * Polar's AccessLink v4 token is the beat-to-beat grant on the person's Polar
 * account. Connecting Polar asks for it first, in the same flow, so its own card
 * on a pairing page only invites linking it alone (which syncs nothing) or after
 * Polar (too late for the first backfill).
 */
export const ADD_ON_PROVIDERS: Record<string, string> = {
  polar_v4: 'polar',
};

export function isAddOnProvider(providerId: string): boolean {
  return providerId in ADD_ON_PROVIDERS;
}

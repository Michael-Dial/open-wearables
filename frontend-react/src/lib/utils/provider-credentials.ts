import type { ProviderAppField } from '@/lib/api/types';

/**
 * What the admin has typed for one field. An input holds only the value saved in
 * the dashboard (never the environment's, which shows as the placeholder), so an
 * empty input always means "use the environment".
 */
export interface FieldDraft {
  value: string;
  /** Remove the saved value. Used for secrets, whose saved value is never shown. */
  clear: boolean;
}

export function initialDraft(field: ProviderAppField): FieldDraft {
  const saved =
    field.kind !== 'secret' && field.source === 'dashboard'
      ? (field.value ?? '')
      : '';
  return { value: saved, clear: false };
}

export function initialDrafts(
  fields: ProviderAppField[]
): Record<string, FieldDraft> {
  return Object.fromEntries(fields.map((f) => [f.key, initialDraft(f)]));
}

/** Whether there is a saved value to remove (including an unreadable secret). */
export function canClear(field: ProviderAppField): boolean {
  return field.source === 'dashboard' || field.unreadable;
}

/**
 * The PUT body: only fields that changed. A string saves it, null removes the
 * saved value so the environment applies again.
 */
export function buildCredentialsUpdate(
  fields: ProviderAppField[],
  drafts: Record<string, FieldDraft>
): Record<string, string | null> {
  const values: Record<string, string | null> = {};
  for (const field of fields) {
    const draft = drafts[field.key];
    if (!draft) continue;
    const typed = draft.value.trim();
    if (draft.clear) {
      if (canClear(field)) values[field.key] = null;
    } else if (field.kind === 'secret') {
      if (typed) values[field.key] = typed;
    } else if (typed !== initialDraft(field).value.trim()) {
      values[field.key] = typed || null;
    }
  }
  return values;
}

/** Short label for where the value in use comes from. */
export function sourceLabel(
  field: Pick<ProviderAppField, 'kind' | 'env_var'>,
  source: ProviderAppField['source']
): string {
  switch (source) {
    case 'dashboard':
      return 'Saved here';
    case 'environment':
      return field.env_var ? `From ${field.env_var}` : 'From environment';
    case 'derived':
      return 'Derived from SECRET_KEY';
    case 'default':
      return field.kind === 'url' ? 'Built from API_BASE_URL' : 'Default';
    case 'unset':
      return 'Not set';
  }
}

/** Placeholder of a field's input: what applies while the input is empty. */
export function fieldPlaceholder(field: ProviderAppField): string {
  if (field.kind !== 'secret') {
    return field.fallback_value ?? 'Not set';
  }
  const hint = field.hint ? ` (${field.hint})` : '';
  if (field.source === 'dashboard') return `Saved${hint} · type to replace`;
  if (!field.is_set) return 'Not set';
  return `${sourceLabel(field, field.source)}${hint} · type to override`;
}

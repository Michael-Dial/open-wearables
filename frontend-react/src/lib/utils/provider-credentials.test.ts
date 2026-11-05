import { describe, expect, it } from 'vitest';
import type { ProviderAppField } from '@/lib/api/types';
import {
  buildCredentialsUpdate,
  fieldPlaceholder,
  initialDrafts,
  sourceLabel,
} from './provider-credentials';

function field(overrides: Partial<ProviderAppField>): ProviderAppField {
  return {
    key: 'whoop_client_id',
    label: 'Client ID',
    kind: 'text',
    description: null,
    env_var: 'WHOOP_CLIENT_ID',
    source: 'environment',
    is_set: true,
    value: 'env-id',
    hint: null,
    fallback_source: 'environment',
    fallback_value: 'env-id',
    updated_at: null,
    unreadable: false,
    ...overrides,
  };
}

const envId = field({});
const savedId = field({
  source: 'dashboard',
  value: 'ui-id',
  fallback_value: 'env-id',
});
const envSecret = field({
  key: 'whoop_client_secret',
  kind: 'secret',
  env_var: 'WHOOP_CLIENT_SECRET',
  value: null,
  hint: '…abcd',
  fallback_value: null,
});
const savedSecret = field({ ...envSecret, source: 'dashboard' });

describe('initialDrafts', () => {
  it('shows only dashboard values, never the environment or a secret', () => {
    expect(initialDrafts([envId, savedId, savedSecret])).toEqual({
      whoop_client_id: { value: 'ui-id', clear: false },
      whoop_client_secret: { value: '', clear: false },
    });
  });
});

describe('buildCredentialsUpdate', () => {
  it('sends nothing when nothing changed', () => {
    const fields = [savedId, savedSecret];
    expect(buildCredentialsUpdate(fields, initialDrafts(fields))).toEqual({});
  });

  it('saves a typed value, trimmed', () => {
    expect(
      buildCredentialsUpdate([envId], {
        whoop_client_id: { value: '  new-id ', clear: false },
      })
    ).toEqual({ whoop_client_id: 'new-id' });
  });

  it('clears a dashboard value when its input is emptied', () => {
    expect(
      buildCredentialsUpdate([savedId], {
        whoop_client_id: { value: '', clear: false },
      })
    ).toEqual({ whoop_client_id: null });
  });

  it('replaces a secret only when one is typed, and clears it on request', () => {
    expect(
      buildCredentialsUpdate([savedSecret], {
        whoop_client_secret: { value: 's3cret', clear: false },
      })
    ).toEqual({ whoop_client_secret: 's3cret' });
    expect(
      buildCredentialsUpdate([savedSecret], {
        whoop_client_secret: { value: '', clear: true },
      })
    ).toEqual({ whoop_client_secret: null });
  });

  it('ignores a clear request when nothing is saved, unless it is unreadable', () => {
    const drafts = { whoop_client_secret: { value: '', clear: true } };
    expect(buildCredentialsUpdate([envSecret], drafts)).toEqual({});
    expect(
      buildCredentialsUpdate([{ ...envSecret, unreadable: true }], drafts)
    ).toEqual({ whoop_client_secret: null });
  });
});

describe('labels', () => {
  it('names where a value comes from', () => {
    expect(sourceLabel(envId, 'environment')).toBe('From WHOOP_CLIENT_ID');
    expect(sourceLabel({ kind: 'url', env_var: null }, 'default')).toBe(
      'Built from API_BASE_URL'
    );
  });

  it('describes a secret without revealing it', () => {
    expect(fieldPlaceholder(envSecret)).toBe(
      'From WHOOP_CLIENT_SECRET (…abcd) · type to override'
    );
    expect(fieldPlaceholder(savedSecret)).toBe(
      'Saved (…abcd) · type to replace'
    );
    expect(fieldPlaceholder(envId)).toBe('env-id');
  });
});

// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { renderToString } from 'react-dom/server';
import type { UserCreatedBy } from '@/lib/api/types';
import {
  CreatedByBadge,
  CreatedByFilterSelect,
  createdByDisplay,
  createdByFilterOptions,
} from './created-by';

const KEY_ID = '3f0b8a52-6a0e-4d55-9c7e-0a4b1d2c3e4f';

const byKey = (label: string, apiKeyId: string | null): UserCreatedBy => ({
  via: 'api_key',
  label,
  api_key_id: apiKeyId,
});

describe('createdByDisplay', () => {
  it('names an API key by the label the API resolved', () => {
    const display = createdByDisplay(byKey('Sleep Hub', KEY_ID));
    expect(display.label).toBe('Sleep Hub');
    expect(display.known).toBe(true);
    expect(display.description).toBe('Created with the API key "Sleep Hub"');
  });

  it('says when the creating key has since been deleted', () => {
    const display = createdByDisplay(byKey('AMS', null));
    expect(display.label).toBe('AMS');
    expect(display.description).toContain('since deleted');
  });

  it('reads the portal and the seed generator', () => {
    expect(
      createdByDisplay({ via: 'portal', label: 'Portal', api_key_id: null })
        .label
    ).toBe('Portal');
    expect(
      createdByDisplay({ via: 'seed', label: 'Seed data', api_key_id: null })
        .label
    ).toBe('Seed data');
  });

  it('is unknown for users created before attribution was recorded', () => {
    const display = createdByDisplay({
      via: null,
      label: null,
      api_key_id: null,
    });
    expect(display).toMatchObject({ label: 'Unknown', known: false });
  });

  it('is unknown when an older backend sends no created_by at all', () => {
    expect(createdByDisplay(undefined)).toMatchObject({
      label: 'Unknown',
      known: false,
    });
  });
});

describe('CreatedByBadge', () => {
  it('shows the app name for a user created with its key', () => {
    const html = renderToString(
      <CreatedByBadge createdBy={byKey('Sleep Hub', KEY_ID)} />
    );
    expect(html).toContain('Sleep Hub');
  });

  it('mutes an unknown creator', () => {
    const html = renderToString(<CreatedByBadge createdBy={undefined} />);
    expect(html).toContain('Unknown');
    expect(html).toContain('text-muted-foreground');
  });
});

describe('createdByFilterOptions', () => {
  it('lists each API key by name, sorted, then the other creators', () => {
    const options = createdByFilterOptions([
      { id: 'b', name: 'Sleep Hub' },
      { id: 'a', name: 'AMS' },
    ]);
    expect(options.apiKeys).toEqual([
      { value: 'a', label: 'AMS' },
      { value: 'b', label: 'Sleep Hub' },
    ]);
    expect(options.others.map((o) => o.value)).toEqual([
      'api_key',
      'portal',
      'seed',
      'unknown',
    ]);
  });
});

describe('CreatedByFilterSelect', () => {
  it('offers every creator and no API key group without keys', () => {
    const html = renderToString(
      <CreatedByFilterSelect
        value={undefined}
        apiKeys={[]}
        onChange={() => {}}
      />
    );
    expect(html).toContain('All creators');
    expect(html).toContain('Seed data');
    expect(html).not.toContain('label="API keys"');
  });
});

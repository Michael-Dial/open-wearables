import {
  KeyRound,
  LayoutDashboard,
  Sprout,
  type LucideIcon,
} from 'lucide-react';
import type {
  ApiKey,
  UserCreatedBy,
  UserCreatedByFilter,
} from '@/lib/api/types';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export interface CreatedByDisplay {
  label: string;
  /** Why the label reads as it does, for the hover title. */
  description: string;
  icon: LucideIcon | null;
  known: boolean;
}

/**
 * What to show for the app that created a user.
 *
 * The backend names the creator from the credential that sent the request: an
 * API key by its name, a developer's dashboard session as the portal, and the
 * seed generator as seed data. Users created before it recorded this have no
 * creator, and neither does a response from an older backend.
 */
export function createdByDisplay(
  createdBy: UserCreatedBy | null | undefined
): CreatedByDisplay {
  switch (createdBy?.via) {
    case 'api_key': {
      const label = createdBy.label || 'API key';
      return {
        label,
        description: createdBy.api_key_id
          ? `Created with the API key "${label}"`
          : `Created with the API key "${label}", since deleted`,
        icon: KeyRound,
        known: true,
      };
    }
    case 'portal':
      return {
        label: createdBy.label || 'Portal',
        description: 'Created by a developer in the portal',
        icon: LayoutDashboard,
        known: true,
      };
    case 'seed':
      return {
        label: createdBy.label || 'Seed data',
        description: 'Created by the seed data generator',
        icon: Sprout,
        known: true,
      };
    default:
      return {
        label: 'Unknown',
        description:
          'Created before Open Wearables recorded which app creates a user',
        icon: null,
        known: false,
      };
  }
}

export function CreatedByBadge({
  createdBy,
  className,
}: {
  createdBy: UserCreatedBy | null | undefined;
  className?: string;
}) {
  const display = createdByDisplay(createdBy);

  if (!display.known) {
    return (
      <span
        className={cn('text-xs text-muted-foreground/70', className)}
        title={display.description}
      >
        {display.label}
      </span>
    );
  }

  const Icon = display.icon;
  return (
    <Badge
      variant="outline"
      className={cn('max-w-48 gap-1 px-1.5 py-0 text-[10px]', className)}
      title={display.description}
    >
      {Icon && <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />}
      <span className="truncate">{display.label}</span>
    </Badge>
  );
}

export interface CreatedByFilterOption {
  value: UserCreatedByFilter;
  label: string;
}

/**
 * Choices for the user list's `created_by` filter: one per API key, then the
 * creators that are not a key. "Any API key" also finds users whose key has
 * been deleted, which no single key's entry can.
 */
export function createdByFilterOptions(
  apiKeys: Pick<ApiKey, 'id' | 'name'>[]
): { apiKeys: CreatedByFilterOption[]; others: CreatedByFilterOption[] } {
  return {
    apiKeys: [...apiKeys]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((key) => ({ value: key.id, label: key.name || 'Unnamed key' })),
    others: [
      { value: 'api_key', label: 'Any API key' },
      { value: 'portal', label: 'Portal' },
      { value: 'seed', label: 'Seed data' },
      { value: 'unknown', label: 'Unknown' },
    ],
  };
}

export function CreatedByFilterSelect({
  value,
  apiKeys,
  onChange,
}: {
  value: UserCreatedByFilter | undefined;
  apiKeys: Pick<ApiKey, 'id' | 'name'>[];
  onChange: (value: UserCreatedByFilter | undefined) => void;
}) {
  const options = createdByFilterOptions(apiKeys);
  return (
    <select
      aria-label="Filter by the app that created the user"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || undefined)}
      className="h-9 rounded-md border border-border/60 bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ring"
    >
      <option value="">All creators</option>
      {options.apiKeys.length > 0 && (
        <optgroup label="API keys">
          {options.apiKeys.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label="Other">
        {options.others.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </optgroup>
    </select>
  );
}

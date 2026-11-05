import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  Copy,
  KeyRound,
  Loader2,
  RotateCcw,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  useProviderCredentials,
  useUpdateProviderCredentials,
} from '@/hooks/api/use-oauth-providers';
import type { Provider, ProviderAppField } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { copyToClipboard } from '@/lib/utils/clipboard';
import {
  buildCredentialsUpdate,
  canClear,
  fieldPlaceholder,
  initialDrafts,
  sourceLabel,
  type FieldDraft,
} from '@/lib/utils/provider-credentials';

interface ProviderCredentialsDialogProps {
  provider: Provider;
}

/**
 * Edit a provider's app registration (client ID and secret, redirect URI,
 * scopes, webhook tokens) without touching the backend's .env. A value saved
 * here overrides the environment; an empty field falls back to it.
 */
export function ProviderCredentialsDialog({
  provider,
}: ProviderCredentialsDialogProps) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, error, refetch } = useProviderCredentials(
    provider.provider,
    open
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <KeyRound className="h-4 w-4" />
          App settings
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{provider.name} app settings</DialogTitle>
          <DialogDescription>
            The credentials Open Wearables uses for its {provider.name}{' '}
            developer app. A value saved here overrides the backend environment;
            leave a field empty to use the environment&apos;s value (shown
            greyed out). Changes reach every API and worker process within about
            15 seconds.
          </DialogDescription>
        </DialogHeader>

        {isLoading && (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && (
          <div className="py-6 text-center text-sm text-muted-foreground">
            <p className="mb-3">Couldn&apos;t load the app settings.</p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        )}

        {/* Mounted once the data is in and unmounted on close, so the drafts start
            from what is saved on every open and a background refetch never wipes
            what is being typed. */}
        {data && (
          <CredentialsForm
            provider={provider.provider}
            fields={data.fields}
            onDone={() => setOpen(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

interface CredentialsFormProps {
  provider: string;
  fields: ProviderAppField[];
  onDone: () => void;
}

function CredentialsForm({ provider, fields, onDone }: CredentialsFormProps) {
  const update = useUpdateProviderCredentials(provider);
  const [drafts, setDrafts] = useState<Record<string, FieldDraft>>(() =>
    initialDrafts(fields)
  );
  const changes = useMemo(
    () => buildCredentialsUpdate(fields, drafts),
    [fields, drafts]
  );
  const changeCount = Object.keys(changes).length;

  const setDraft = (key: string, next: Partial<FieldDraft>) =>
    setDrafts((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? { value: '', clear: false }), ...next },
    }));

  const handleSave = () => {
    if (changeCount === 0) return;
    // Failure is reported by the hook's toast; the form stays open with what was typed.
    update.mutate({ values: changes }, { onSuccess: onDone });
  };

  return (
    <>
      <div className="space-y-5">
        {fields.map((field) => (
          <CredentialFieldInput
            key={field.key}
            field={field}
            draft={drafts[field.key] ?? { value: '', clear: false }}
            change={changes[field.key]}
            onChange={(next) => setDraft(field.key, next)}
          />
        ))}
        <p className="text-[11px] text-muted-foreground">
          Pointing at a different developer app (a new client ID) means accounts
          connected through the old one have to reconnect. Webhook subscriptions
          created with an earlier token or client keep using it until they are
          registered again.
        </p>
      </div>

      <DialogFooter className="gap-2">
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button
          disabled={changeCount === 0 || update.isPending}
          onClick={handleSave}
        >
          {update.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Saving…
            </>
          ) : changeCount > 0 ? (
            `Save ${changeCount} change${changeCount > 1 ? 's' : ''}`
          ) : (
            'Save'
          )}
        </Button>
      </DialogFooter>
    </>
  );
}

interface CredentialFieldInputProps {
  field: ProviderAppField;
  draft: FieldDraft;
  /** This field's entry in the pending update: a value, null (remove), or none. */
  change: string | null | undefined;
  onChange: (next: Partial<FieldDraft>) => void;
}

function CredentialFieldInput({
  field,
  draft,
  change,
  onChange,
}: CredentialFieldInputProps) {
  const id = `credential-${field.key}`;
  const isSecret = field.kind === 'secret';
  const clearing = draft.clear;
  // Where the value will come from once saved.
  const pendingSource =
    change === undefined
      ? field.source
      : change === null
        ? field.fallback_source
        : 'dashboard';

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label htmlFor={id}>{field.label}</Label>
        <span
          className={cn(
            'inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium',
            pendingSource === 'dashboard'
              ? 'bg-indigo-500/15 text-indigo-300'
              : pendingSource === 'unset'
                ? 'bg-amber-500/15 text-amber-300'
                : 'bg-muted text-muted-foreground'
          )}
        >
          {sourceLabel(field, pendingSource)}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Input
          id={id}
          type={isSecret ? 'password' : 'text'}
          autoComplete="off"
          spellCheck={false}
          value={clearing ? '' : draft.value}
          disabled={clearing}
          placeholder={
            clearing
              ? `Will use ${sourceLabel(field, field.fallback_source).toLowerCase()}`
              : fieldPlaceholder(field)
          }
          onChange={(e) => onChange({ value: e.target.value, clear: false })}
          className="font-mono text-xs md:text-xs"
        />
        {canClear(field) && (
          <Button
            type="button"
            variant="ghost-faded"
            size="icon-sm"
            title={
              clearing
                ? 'Keep the saved value'
                : 'Remove the saved value and use the environment'
            }
            aria-label={
              clearing
                ? `Keep the saved ${field.label}`
                : `Use the environment's ${field.label}`
            }
            aria-pressed={clearing}
            onClick={() =>
              isSecret
                ? onChange({ clear: !clearing, value: '' })
                : onChange({ value: '', clear: false })
            }
          >
            <RotateCcw className="h-4 w-4" />
          </Button>
        )}
        {field.kind === 'url' && field.value && (
          <Button
            type="button"
            variant="ghost-faded"
            size="icon-sm"
            title="Copy the redirect URI in use"
            aria-label={`Copy ${field.label}`}
            onClick={() => copyToClipboard(field.value ?? '')}
          >
            <Copy className="h-4 w-4" />
          </Button>
        )}
      </div>

      {field.unreadable && (
        <p className="flex items-start gap-1.5 text-[11px] text-amber-300">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />A value saved here
          can&apos;t be decrypted (SECRET_KEY changed), so it is ignored. Enter
          it again, or remove it.
        </p>
      )}
      {field.kind === 'url' && field.value && (
        <p className="break-all text-[11px] text-muted-foreground">
          In use: <span className="font-mono">{field.value}</span>
        </p>
      )}
      {field.description && (
        <p className="text-[11px] text-muted-foreground">{field.description}</p>
      )}
    </div>
  );
}

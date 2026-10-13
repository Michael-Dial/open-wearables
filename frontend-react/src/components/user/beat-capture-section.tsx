import { useState } from 'react';
import { Download, HeartPulse, Loader2, X } from 'lucide-react';
import { toast } from 'sonner';
import {
  useBeatCaptureSetting,
  useBeatCaptureWindows,
  useCancelBeatCaptureWindow,
  useCreateBeatCaptureWindow,
  useUpdateBeatCaptureSetting,
} from '@/hooks/api/use-beat-capture';
import {
  beatCaptureService,
  type BeatCaptureMode,
  type BeatCaptureSetting,
  type BeatCaptureWindow,
} from '@/lib/api/services/beat-capture.service';
import { getErrorMessage } from '@/lib/errors/handler';
import { saveBlob } from '@/lib/utils/download';
import { beatsFilename, beatsToCsv, localInputToIso } from '@/lib/utils/beats';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

interface BeatCaptureSectionProps {
  userId: string;
}

const MODES: { value: BeatCaptureMode; label: string; detail: string }[] = [
  {
    value: 'off',
    label: 'Off',
    detail: 'Nothing automatically. Requested windows are still uploaded.',
  },
  {
    value: 'sleep',
    label: 'During sleep',
    detail:
      'Every heartbeat series overlapping a sleep session, padded either side.',
  },
  {
    value: 'all_day',
    label: 'All day',
    detail: 'Every heartbeat series the watch records.',
  },
];

const STATUS_VARIANT = {
  pending: 'warning',
  fulfilled: 'success',
  cancelled: 'outline',
} as const;

function formatRange(startIso: string, endIso: string): string {
  const start = new Date(startIso);
  const end = new Date(endIso);
  const sameDay = start.toDateString() === end.toDateString();
  return `${start.toLocaleString()} – ${
    sameDay ? end.toLocaleTimeString() : end.toLocaleString()
  }`;
}

function CollectionSettings({ userId }: BeatCaptureSectionProps) {
  const { data: setting, isLoading } = useBeatCaptureSetting(userId);

  if (isLoading || !setting) {
    return <Skeleton className="h-48 w-full rounded-lg" />;
  }
  // Keyed on the save time, so a save (or another tab's) restarts the form
  // from what is stored.
  return (
    <CollectionForm
      key={setting.updated_at ?? 'default'}
      userId={userId}
      setting={setting}
    />
  );
}

function CollectionForm({
  userId,
  setting,
}: BeatCaptureSectionProps & { setting: BeatCaptureSetting }) {
  const update = useUpdateBeatCaptureSetting(userId);
  const [mode, setMode] = useState<BeatCaptureMode>(setting.mode);
  const [lookback, setLookback] = useState(String(setting.lookback_days));
  const [padding, setPadding] = useState(String(setting.sleep_padding_minutes));

  const lookbackDays = Number(lookback);
  const paddingMinutes = Number(padding);
  const valid =
    Number.isInteger(lookbackDays) &&
    lookbackDays >= 1 &&
    lookbackDays <= 30 &&
    Number.isInteger(paddingMinutes) &&
    paddingMinutes >= 0 &&
    paddingMinutes <= 240;
  const dirty =
    setting.is_default ||
    mode !== setting.mode ||
    lookbackDays !== setting.lookback_days ||
    paddingMinutes !== setting.sleep_padding_minutes;

  return (
    <div className="space-y-4 rounded-lg border bg-card/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">Automatic collection</h3>
        {setting.is_default && (
          <Badge variant="outline">
            Instance default, not set for this user
          </Badge>
        )}
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        {MODES.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setMode(option.value)}
            className={cn(
              'rounded-lg border p-3 text-left transition-colors',
              mode === option.value
                ? 'border-primary bg-primary/10'
                : 'border-border/50 hover:border-primary/50'
            )}
            aria-pressed={mode === option.value}
          >
            <div className="text-sm font-medium">{option.label}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {option.detail}
            </div>
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="beat-lookback">Lookback (days, 1–30)</Label>
          <Input
            id="beat-lookback"
            type="number"
            min={1}
            max={30}
            value={lookback}
            onChange={(e) => setLookback(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="beat-padding">Sleep padding (minutes, 0–240)</Label>
          <Input
            id="beat-padding"
            type="number"
            min={0}
            max={240}
            value={padding}
            disabled={mode !== 'sleep'}
            onChange={(e) => setPadding(e.target.value)}
          />
        </div>
      </div>

      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-muted-foreground">
          The phone reads this after each sync. Beats are Apple Watch
          pulse-to-pulse intervals (optical), stored as{' '}
          <code>pulse_to_pulse_interval</code>, never as ECG R-R.
        </p>
        <Button
          size="sm"
          disabled={!valid || !dirty || update.isPending}
          onClick={() =>
            update.mutate({
              mode,
              lookback_days: lookbackDays,
              sleep_padding_minutes: paddingMinutes,
            })
          }
        >
          {update.isPending && <Loader2 className="animate-spin" />}
          Save
        </Button>
      </div>
    </div>
  );
}

function useBeatDownload(userId: string) {
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const download = async (key: string, startIso: string, endIso: string) => {
    setBusyKey(key);
    try {
      const beats = await beatCaptureService.fetchBeats(
        userId,
        startIso,
        endIso
      );
      if (beats.length === 0) {
        toast.info('No beats stored for this span yet.');
        return;
      }
      saveBlob(
        new Blob([beatsToCsv(beats)], { type: 'text/csv' }),
        beatsFilename(userId, startIso)
      );
      const gaps = beats.filter((beat) => beat.preceded_by_gap).length;
      toast.success(
        `${beats.length} beats downloaded${gaps ? `, ${gaps} after a gap` : ''}.`
      );
    } catch (error) {
      toast.error(`Download failed: ${getErrorMessage(error)}`);
    } finally {
      setBusyKey(null);
    }
  };

  return { busyKey, download };
}

function SpanForm({
  userId,
  download,
  busy,
}: BeatCaptureSectionProps & {
  download: (key: string, start: string, end: string) => Promise<void>;
  busy: boolean;
}) {
  const create = useCreateBeatCaptureWindow(userId);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [note, setNote] = useState('');

  const startIso = localInputToIso(start);
  const endIso = localInputToIso(end);
  const valid = !!startIso && !!endIso && endIso > startIso;

  return (
    <div className="space-y-4 rounded-lg border bg-card/40 p-4">
      <div>
        <h3 className="font-medium">A specific span</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Request a span (a PSG night, a lab visit) and the phone uploads every
          heartbeat series in it on its next sync, whatever the mode. Opening
          the app makes that sync immediate. Times are in your browser&apos;s
          zone. A request may cover at most 7 days.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="beat-start">Start</Label>
          <Input
            id="beat-start"
            type="datetime-local"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="beat-end">End</Label>
          <Input
            id="beat-end"
            type="datetime-local"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="beat-note">Note</Label>
          <Input
            id="beat-note"
            maxLength={255}
            placeholder="PSG night 3"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!valid || busy}
          onClick={() => download('span', startIso!, endIso!)}
        >
          {busy ? <Loader2 className="animate-spin" /> : <Download />}
          Download stored beats
        </Button>
        <Button
          size="sm"
          disabled={!valid || create.isPending}
          onClick={() =>
            create.mutate(
              {
                start_at: startIso!,
                end_at: endIso!,
                ...(note.trim() ? { note: note.trim() } : {}),
              },
              {
                onSuccess: () => {
                  setStart('');
                  setEnd('');
                  setNote('');
                },
              }
            )
          }
        >
          {create.isPending && <Loader2 className="animate-spin" />}
          Request from phone
        </Button>
      </div>
    </div>
  );
}

function WindowRow({
  span,
  onCancel,
  cancelling,
  onDownload,
  downloading,
}: {
  span: BeatCaptureWindow;
  onCancel: () => void;
  cancelling: boolean;
  onDownload: () => void;
  downloading: boolean;
}) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap">
        {formatRange(span.start_at, span.end_at)}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {span.note ?? '—'}
      </TableCell>
      <TableCell>
        <Badge variant={STATUS_VARIANT[span.status]} className="capitalize">
          {span.status}
        </Badge>
      </TableCell>
      <TableCell className="tabular-nums">
        {span.status === 'fulfilled'
          ? `${span.beats_reported ?? 0} beats · ${span.series_reported ?? 0} series`
          : '—'}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            title="Download stored beats (CSV)"
            disabled={downloading}
            onClick={onDownload}
          >
            {downloading ? <Loader2 className="animate-spin" /> : <Download />}
          </Button>
          {span.status === 'pending' && (
            <Button
              variant="ghost"
              size="icon-sm"
              title="Cancel request"
              disabled={cancelling}
              onClick={onCancel}
            >
              <X />
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function RequestedWindows({
  userId,
  download,
  busyKey,
}: BeatCaptureSectionProps & {
  download: (key: string, start: string, end: string) => Promise<void>;
  busyKey: string | null;
}) {
  const { data: windows, isLoading } = useBeatCaptureWindows(userId);
  const cancel = useCancelBeatCaptureWindow(userId);

  if (isLoading) return <Skeleton className="h-32 w-full rounded-lg" />;
  if (!windows?.length) {
    return (
      <p className="text-sm text-muted-foreground">No spans requested yet.</p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Zero beats on a fulfilled request means the watch recorded no heartbeat
        series in that span, or the participant has not granted Heartbeat
        Series: HealthKit does not tell the app which.
      </p>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Span</TableHead>
              <TableHead>Note</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Phone reported</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {windows.map((span) => (
              <WindowRow
                key={span.id}
                span={span}
                cancelling={cancel.isPending && cancel.variables === span.id}
                onCancel={() => cancel.mutate(span.id)}
                downloading={busyKey === span.id}
                onDownload={() => download(span.id, span.start_at, span.end_at)}
              />
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

export function BeatCaptureSection({ userId }: BeatCaptureSectionProps) {
  const { busyKey, download } = useBeatDownload(userId);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <HeartPulse className="h-5 w-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold">Beat-to-beat capture</h2>
      </div>
      <CollectionSettings userId={userId} />
      <SpanForm userId={userId} download={download} busy={busyKey === 'span'} />
      <RequestedWindows userId={userId} download={download} busyKey={busyKey} />
    </div>
  );
}

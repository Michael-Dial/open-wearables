import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  beatCaptureService,
  type BeatCaptureSettingUpdate,
  type BeatCaptureWindowCreate,
} from '@/lib/api/services/beat-capture.service';
import { queryKeys } from '@/lib/query/keys';
import { getErrorMessage } from '@/lib/errors/handler';

export function useBeatCaptureSetting(userId: string) {
  return useQuery({
    queryKey: queryKeys.beatCapture.setting(userId),
    queryFn: () => beatCaptureService.getSetting(userId),
    enabled: !!userId,
  });
}

export function useBeatCaptureWindows(userId: string) {
  return useQuery({
    queryKey: queryKeys.beatCapture.windows(userId),
    queryFn: () => beatCaptureService.listWindows(userId),
    enabled: !!userId,
  });
}

export function useUpdateBeatCaptureSetting(userId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: BeatCaptureSettingUpdate) =>
      beatCaptureService.updateSetting(userId, data),
    onSuccess: (setting) => {
      queryClient.setQueryData(queryKeys.beatCapture.setting(userId), setting);
      toast.success(
        'Beat capture updated. The phone applies it on its next sync.'
      );
    },
    onError: (error) => {
      toast.error(`Failed to update beat capture: ${getErrorMessage(error)}`);
    },
  });
}

export function useCreateBeatCaptureWindow(userId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: BeatCaptureWindowCreate) =>
      beatCaptureService.createWindow(userId, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.beatCapture.windows(userId),
      });
      toast.success('Window requested. The phone uploads it on its next sync.');
    },
    onError: (error) => {
      toast.error(`Failed to request window: ${getErrorMessage(error)}`);
    },
  });
}

export function useCancelBeatCaptureWindow(userId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (windowId: string) =>
      beatCaptureService.cancelWindow(userId, windowId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.beatCapture.windows(userId),
      });
      toast.success('Window cancelled');
    },
    onError: (error) => {
      toast.error(`Failed to cancel window: ${getErrorMessage(error)}`);
    },
  });
}

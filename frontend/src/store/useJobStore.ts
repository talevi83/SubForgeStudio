import { create } from 'zustand';
import { api } from '../services/api';
import type {
  Job,
  SystemInfo,
  ProbeResponse,
  JobConfig,
  SubtitleSegment,
  WebSocketEventPayload,
} from '../types';

interface JobState {
  systemInfo: SystemInfo | null;
  jobs: Job[];
  activeJob: Job | null;
  probeResult: ProbeResponse | null;
  pipelineConfig: JobConfig;
  wsConnected: boolean;
  videoCurrentTime: number;
  selectedSegmentIndex: number | null;
  subtitleOverlayMode: 'original' | 'translated' | 'dual';
  applyRtlExport: boolean;
  isLoading: boolean;
  error: string | null;

  // Actions
  fetchSystemInfo: () => Promise<void>;
  fetchJobs: () => Promise<void>;
  selectJob: (jobId: string) => Promise<void>;
  clearActiveJob: () => void;
  setProbeResult: (res: ProbeResponse | null) => void;
  updatePipelineConfig: (config: Partial<JobConfig>) => void;
  createAndStartJob: () => Promise<Job | null>;
  cancelActiveJob: () => Promise<void>;
  deleteJob: (jobId: string) => Promise<void>;
  updateSegment: (segmentId: number, patch: Partial<SubtitleSegment>) => Promise<void>;
  setVideoCurrentTime: (time: number) => void;
  setSelectedSegmentIndex: (index: number | null) => void;
  setSubtitleOverlayMode: (mode: 'original' | 'translated' | 'dual') => void;
  setApplyRtlExport: (val: boolean) => void;
  connectWebSocket: (jobId: string) => void;
  disconnectWebSocket: () => void;
}

let activeSocket: WebSocket | null = null;

export const useJobStore = create<JobState>((set, get) => ({
  systemInfo: null,
  jobs: [],
  activeJob: null,
  probeResult: null,
  pipelineConfig: {
    selected_audio_stream: 0,
    whisper_model: 'medium',
    source_language: 'auto',
    vad_filter: true,
    enable_translation: true,
    target_language: 'Hebrew',
    gemini_model: 'gemini-2.5-flash',
    gemini_api_key_override: '',
    context_notes: '',
  },
  wsConnected: false,
  videoCurrentTime: 0,
  selectedSegmentIndex: null,
  subtitleOverlayMode: 'dual',
  applyRtlExport: true,
  isLoading: false,
  error: null,

  fetchSystemInfo: async () => {
    try {
      const info = await api.getSystemInfo();
      set({
        systemInfo: info,
        pipelineConfig: {
          ...get().pipelineConfig,
          whisper_model: info.whisper_default_model || 'medium',
          gemini_model: info.gemini_model || 'gemini-2.5-flash',
        },
      });
    } catch (e: any) {
      console.error('Failed to fetch system info:', e);
    }
  },

  fetchJobs: async () => {
    try {
      const jobs = await api.listJobs();
      set({ jobs });
    } catch (e: any) {
      console.error('Failed to list jobs:', e);
    }
  },

  selectJob: async (jobId: string) => {
    try {
      set({ isLoading: true, error: null });
      const job = await api.getJob(jobId);
      set({ activeJob: job, isLoading: false, selectedSegmentIndex: null });
      get().connectWebSocket(jobId);
    } catch (e: any) {
      set({ error: e.message, isLoading: false });
    }
  },

  clearActiveJob: () => {
    get().disconnectWebSocket();
    set({ activeJob: null, probeResult: null, selectedSegmentIndex: null });
  },

  setProbeResult: (probeResult) => {
    set({ probeResult });
  },

  updatePipelineConfig: (config) => {
    set((state) => ({
      pipelineConfig: { ...state.pipelineConfig, ...config },
    }));
  },

  createAndStartJob: async () => {
    const { probeResult, pipelineConfig } = get();
    if (!probeResult) return null;

    try {
      set({ isLoading: true, error: null });
      const newJob = await api.createJob({
        ...pipelineConfig,
        video_path: probeResult.file_path,
        file_name: probeResult.file_name,
      });

      set((state) => ({
        activeJob: newJob,
        jobs: [newJob, ...state.jobs],
        isLoading: false,
      }));

      get().connectWebSocket(newJob.id);
      return newJob;
    } catch (e: any) {
      set({ error: e.message, isLoading: false });
      return null;
    }
  },

  cancelActiveJob: async () => {
    const { activeJob } = get();
    if (!activeJob) return;
    try {
      await api.cancelJob(activeJob.id);
      set((state) => ({
        activeJob: state.activeJob ? { ...state.activeJob, status: 'CANCELLED' } : null,
      }));
    } catch (e: any) {
      console.error('Failed to cancel job:', e);
    }
  },

  deleteJob: async (jobId: string) => {
    try {
      await api.deleteJob(jobId);
      set((state) => ({
        jobs: state.jobs.filter((j) => j.id !== jobId),
        activeJob: state.activeJob?.id === jobId ? null : state.activeJob,
      }));
    } catch (e: any) {
      console.error('Failed to delete job:', e);
    }
  },

  updateSegment: async (segmentId: number, patch: Partial<SubtitleSegment>) => {
    const { activeJob } = get();
    if (!activeJob) return;

    try {
      const updated = await api.updateSegment(activeJob.id, segmentId, patch);
      set((state) => {
        if (!state.activeJob) return state;
        const updatedSegments = state.activeJob.segments.map((s) =>
          s.id === segmentId ? { ...s, ...updated } : s
        );
        return {
          activeJob: {
            ...state.activeJob,
            segments: updatedSegments,
          },
        };
      });
    } catch (e: any) {
      console.error('Failed to update segment:', e);
    }
  },

  setVideoCurrentTime: (time: number) => {
    set({ videoCurrentTime: time });
  },

  setSelectedSegmentIndex: (index: number | null) => {
    set({ selectedSegmentIndex: index });
  },

  setSubtitleOverlayMode: (mode) => {
    set({ subtitleOverlayMode: mode });
  },

  setApplyRtlExport: (val: boolean) => {
    set({ applyRtlExport: val });
  },

  connectWebSocket: (jobId: string) => {
    get().disconnectWebSocket();

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/jobs/${jobId}`;
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      set({ wsConnected: true });
    };

    socket.onmessage = (event) => {
      try {
        const data: WebSocketEventPayload = JSON.parse(event.data);
        const { activeJob } = get();
        if (!activeJob || activeJob.id !== data.job_id) return;

        if (data.event === 'STAGE_PROGRESS') {
          set({
            activeJob: {
              ...activeJob,
              status: (data.stage as any) || activeJob.status,
              progress_percent: data.progress_percent,
              speed_factor: data.speed_factor ?? activeJob.speed_factor,
              eta_seconds: data.eta_seconds ?? activeJob.eta_seconds,
            },
          });
        } else if (data.event === 'SEGMENT_CREATED' && data.segment) {
          const seg = data.segment;
          const existing = activeJob.segments.find((s) => s.index === seg.index);
          const newSegments = existing
            ? activeJob.segments.map((s) => (s.index === seg.index ? { ...s, ...seg } : s))
            : [...activeJob.segments, seg];
          newSegments.sort((a, b) => a.start_time - b.start_time);

          set({
            activeJob: {
              ...activeJob,
              progress_percent: data.progress_percent || activeJob.progress_percent,
              speed_factor: data.speed_factor ?? activeJob.speed_factor,
              eta_seconds: data.eta_seconds ?? activeJob.eta_seconds,
              segments: newSegments,
            },
          });
        } else if (data.event === 'BATCH_TRANSLATED' && data.segments_batch) {
          const batchMap = new Map(data.segments_batch.map((s) => [s.index, s.translated_text]));
          const updated = activeJob.segments.map((s) => {
            if (batchMap.has(s.index)) {
              return { ...s, translated_text: batchMap.get(s.index) };
            }
            return s;
          });
          set({
            activeJob: {
              ...activeJob,
              progress_percent: data.progress_percent || activeJob.progress_percent,
              segments: updated,
            },
          });
        } else if (data.event === 'JOB_COMPLETED') {
          // Re-fetch complete job to ensure consistent IDs and segment records
          api.getJob(data.job_id).then((freshJob) => {
            set({ activeJob: freshJob });
            get().fetchJobs();
          });
        } else if (data.event === 'JOB_FAILED') {
          set({
            activeJob: {
              ...activeJob,
              status: 'FAILED',
              error_message: data.message || 'Pipeline processing failed',
            },
          });
          get().fetchJobs();
        }
      } catch (err) {
        console.error('Failed to parse WS message:', err);
      }
    };

    socket.onclose = () => {
      set({ wsConnected: false });
    };

    activeSocket = socket;
  },

  disconnectWebSocket: () => {
    if (activeSocket) {
      activeSocket.close();
      activeSocket = null;
      set({ wsConnected: false });
    }
  },
}));

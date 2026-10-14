import { create } from 'zustand';
import { api } from '../services/api';
import { isActiveStatus } from '../types';
import type {
  Job,
  JobStatus,
  SystemInfo,
  ProbeResponse,
  JobConfig,
  SubtitleSegment,
  SegmentPatch,
  Glossary,
  RetranslateOptions,
  WebSocketEventPayload,
} from '../types';

const CONFIG_STORAGE_KEY = 'subforge.pipelineConfig';

const DEFAULT_CONFIG: JobConfig = {
  selected_audio_stream: 0,
  whisper_model: 'medium',
  source_language: 'auto',
  vad_filter: true,
  comms_boost: false,
  detect_onscreen_text: false,
  enable_translation: true,
  target_language: 'Hebrew',
  gemini_api_key_override: '',
  context_notes: '',
};

/** Remembered pipeline settings (never the API key). Storage can be unavailable — fail soft. */
function loadSavedConfig(): Partial<JobConfig> {
  try {
    const raw = localStorage.getItem(CONFIG_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    // Older versions stored the Gemini model here; it now lives in Settings
    delete parsed.gemini_model;
    return parsed;
  } catch {
    return {};
  }
}

function saveConfig(config: JobConfig) {
  try {
    const { gemini_api_key_override: _key, selected_audio_stream: _stream, context_notes: _notes, ...rest } = config;
    localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(rest));
  } catch {
    /* ignore */
  }
}

const savedConfig = loadSavedConfig();

interface SeekRequest {
  time: number;
  nonce: number;
  play: boolean;
}

interface JobState {
  systemInfo: SystemInfo | null;
  jobs: Job[];
  activeJob: Job | null;
  probeResult: ProbeResponse | null;
  pipelineConfig: JobConfig;
  wsConnected: boolean;
  stageMessage: string | null;
  videoCurrentTime: number;
  selectedSegmentIndex: number | null;
  seekRequest: SeekRequest | null;
  subtitleOverlayMode: 'original' | 'translated' | 'dual';
  applyRtlExport: boolean;
  isLoading: boolean;
  error: string | null;
  timingBusy: boolean;
  timingNote: string | null;
  timingUndo: { jobId: string; times: { id: number; start_time: number; end_time: number }[] } | null;

  fetchSystemInfo: () => Promise<void>;
  fetchJobs: () => Promise<void>;
  selectJob: (jobId: string) => Promise<void>;
  refreshActiveJob: () => Promise<void>;
  clearActiveJob: () => void;
  setProbeResult: (res: ProbeResponse | null) => void;
  updatePipelineConfig: (config: Partial<JobConfig>) => void;
  createAndStartJob: () => Promise<Job | null>;
  cancelActiveJob: () => Promise<void>;
  retranslateActiveJob: (options: RetranslateOptions) => Promise<void>;
  deleteJob: (jobId: string) => Promise<void>;
  updateSegment: (segmentId: number, patch: SegmentPatch) => Promise<void>;
  replaceAll: (search: string, replacement: string, column: 'original' | 'translated') => Promise<number>;
  saveGlossary: (glossary: Glossary, profile?: string) => Promise<boolean>;
  retimeActiveJob: () => Promise<void>;
  undoRetime: () => Promise<void>;
  moveSegment: (segmentId: number, toPosition: number) => Promise<void>;
  setVideoCurrentTime: (time: number) => void;
  setSelectedSegmentIndex: (index: number | null) => void;
  seekTo: (time: number, play?: boolean) => void;
  setSubtitleOverlayMode: (mode: 'original' | 'translated' | 'dual') => void;
  setApplyRtlExport: (val: boolean) => void;
  clearError: () => void;
  connectWebSocket: (jobId: string, isReconnect?: boolean) => void;
  disconnectWebSocket: () => void;
}

let activeSocket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const mergeSegments = (segments: SubtitleSegment[], updates: SubtitleSegment[]): SubtitleSegment[] => {
  const byIndex = new Map(updates.map((u) => [u.index, u]));
  return segments.map((s) => (byIndex.has(s.index) ? { ...s, ...byIndex.get(s.index)! } : s));
};

export const useJobStore = create<JobState>((set, get) => {
  /** Apply a change to the active job and mirror status/progress into the sidebar list. */
  const patchActiveJob = (patch: Partial<Job>) => {
    const { activeJob, jobs } = get();
    if (!activeJob) return;
    const updated = { ...activeJob, ...patch };
    set({
      activeJob: updated,
      jobs: jobs.map((j) =>
        j.id === updated.id
          ? {
              ...j,
              status: updated.status,
              progress_percent: updated.progress_percent,
              segment_count: updated.segments.length || j.segment_count,
            }
          : j
      ),
    });
  };

  const handleMessage = (data: WebSocketEventPayload) => {
    const { activeJob } = get();
    if (!activeJob || activeJob.id !== data.job_id) return;

    switch (data.event) {
      case 'STAGE_PROGRESS': {
        patchActiveJob({
          status: (data.stage as JobStatus) || activeJob.status,
          progress_percent: data.progress_percent ?? activeJob.progress_percent,
          speed_factor: data.speed_factor ?? activeJob.speed_factor,
          eta_seconds: data.eta_seconds ?? activeJob.eta_seconds,
        });
        if (data.message) set({ stageMessage: data.message });
        break;
      }
      case 'SEGMENT_CREATED': {
        if (!data.segment) break;
        const seg = data.segment;
        const exists = activeJob.segments.some((s) => s.index === seg.index);
        const segments = exists
          ? activeJob.segments.map((s) => (s.index === seg.index ? { ...s, ...seg } : s))
          : [...activeJob.segments, seg];
        patchActiveJob({
          status: 'TRANSCRIBING',
          progress_percent: data.progress_percent || activeJob.progress_percent,
          speed_factor: data.speed_factor ?? activeJob.speed_factor,
          eta_seconds: data.eta_seconds ?? activeJob.eta_seconds,
          segments,
        });
        break;
      }
      case 'SEGMENTS_REFINED': {
        // Raw Whisper segments are replaced by the final, editable (id-bearing) subtitles
        patchActiveJob({
          segments: data.segments_batch || [],
          progress_percent: data.progress_percent || activeJob.progress_percent,
        });
        break;
      }
      case 'BATCH_TRANSLATED': {
        if (!data.segments_batch) break;
        patchActiveJob({ segments: mergeSegments(activeJob.segments, data.segments_batch) });
        break;
      }
      case 'JOB_COMPLETED':
      case 'JOB_FAILED':
      case 'JOB_CANCELLED': {
        set({ stageMessage: null });
        get().refreshActiveJob();
        get().fetchJobs();
        break;
      }
    }
  };

  return {
    systemInfo: null,
    jobs: [],
    activeJob: null,
    probeResult: null,
    pipelineConfig: { ...DEFAULT_CONFIG, ...savedConfig },
    wsConnected: false,
    stageMessage: null,
    videoCurrentTime: 0,
    selectedSegmentIndex: null,
    seekRequest: null,
    subtitleOverlayMode: 'dual',
    applyRtlExport: true,
    isLoading: false,
    error: null,
    timingBusy: false,
    timingNote: null,
    timingUndo: null,

    fetchSystemInfo: async () => {
      try {
        const info = await api.getSystemInfo();
        const cfg = get().pipelineConfig;
        set({
          systemInfo: info,
          pipelineConfig: {
            ...cfg,
            // Saved choice wins; otherwise use the server default
            whisper_model: savedConfig.whisper_model || info.whisper_default_model || cfg.whisper_model,
          },
        });
      } catch (e) {
        console.error('Failed to fetch system info:', e);
      }
    },

    fetchJobs: async () => {
      try {
        const jobs = await api.listJobs();
        set({ jobs });
      } catch (e) {
        console.error('Failed to list jobs:', e);
      }
    },

    selectJob: async (jobId: string) => {
      if (get().activeJob?.id === jobId) return;
      try {
        set({ isLoading: true, error: null });
        const job = await api.getJob(jobId);
        set({
          activeJob: job,
          isLoading: false,
          selectedSegmentIndex: null,
          videoCurrentTime: 0,
          seekRequest: null,
          stageMessage: null,
        });
        get().connectWebSocket(jobId);
      } catch (e: any) {
        set({ error: e.message, isLoading: false });
      }
    },

    refreshActiveJob: async () => {
      const id = get().activeJob?.id;
      if (!id) return;
      try {
        const fresh = await api.getJob(id);
        if (get().activeJob?.id === id) set({ activeJob: fresh });
      } catch (e) {
        console.error('Failed to refresh job:', e);
      }
    },

    clearActiveJob: () => {
      get().disconnectWebSocket();
      set({ activeJob: null, probeResult: null, selectedSegmentIndex: null, seekRequest: null, stageMessage: null });
    },

    setProbeResult: (probeResult) => {
      set((state) => ({
        probeResult,
        pipelineConfig: { ...state.pipelineConfig, selected_audio_stream: 0 },
      }));
    },

    updatePipelineConfig: (config) => {
      const next = { ...get().pipelineConfig, ...config };
      saveConfig(next);
      set({ pipelineConfig: next });
    },

    createAndStartJob: async () => {
      const { probeResult, pipelineConfig } = get();
      if (!probeResult) return null;

      try {
        set({ isLoading: true, error: null });
        const newJob = await api.createJob({
          ...pipelineConfig,
          gemini_api_key_override: pipelineConfig.gemini_api_key_override || undefined,
          video_path: probeResult.file_path,
          file_name: probeResult.file_name,
        });

        set((state) => ({
          activeJob: newJob,
          jobs: [newJob, ...state.jobs],
          isLoading: false,
          selectedSegmentIndex: null,
          seekRequest: null,
          videoCurrentTime: 0,
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
        set({ stageMessage: 'Cancelling...' });
      } catch (e: any) {
        set({ error: e.message });
      }
    },

    retranslateActiveJob: async (options) => {
      const { activeJob, pipelineConfig } = get();
      if (!activeJob) return;
      try {
        set({ error: null });
        const job = await api.retranslate(activeJob.id, {
          gemini_api_key_override: pipelineConfig.gemini_api_key_override || undefined,
          ...options,
        });
        // Keep the segments we already have; the job response carries the new status
        patchActiveJob({ ...job, segments: options.only_failed || options.segment_ids ? activeJob.segments : job.segments });
        get().connectWebSocket(activeJob.id);
      } catch (e: any) {
        set({ error: e.message });
      }
    },

    deleteJob: async (jobId: string) => {
      try {
        await api.deleteJob(jobId);
        if (get().activeJob?.id === jobId) get().clearActiveJob();
        set((state) => ({ jobs: state.jobs.filter((j) => j.id !== jobId) }));
      } catch (e: any) {
        set({ error: e.message });
      }
    },

    updateSegment: async (segmentId, patch) => {
      const { activeJob } = get();
      if (!activeJob) return;
      const jobId = activeJob.id;

      // Optimistic: show the change immediately, the server only confirms derived fields
      patchActiveJob({
        segments: activeJob.segments.map((s) => (s.id === segmentId ? { ...s, ...patch } : s)),
      });

      try {
        const saved = await api.updateSegment(jobId, segmentId, patch);
        const current = get().activeJob;
        if (!current || current.id !== jobId) return;
        set({
          activeJob: {
            ...current,
            segments: current.segments.map((s) =>
              s.id === segmentId
                ? {
                    ...s,
                    // Text may have changed again while the request was in flight — keep local text
                    cps: saved.cps,
                    translation_failed: saved.translation_failed,
                    start_time: patch.start_time !== undefined ? saved.start_time : s.start_time,
                    end_time: patch.end_time !== undefined ? saved.end_time : s.end_time,
                  }
                : s
            ),
          },
        });
      } catch (e: any) {
        set({ error: `Could not save subtitle: ${e.message}` });
        get().refreshActiveJob();
      }
    },

    saveGlossary: async (glossary, profile) => {
      const { activeJob } = get();
      if (!activeJob) return false;
      try {
        const saved = await api.saveGlossary(activeJob.id, glossary, profile);
        patchActiveJob({
          glossary: saved,
          ...(profile !== undefined ? { glossary_profile: profile.trim() || null } : {}),
        });
        return true;
      } catch (e: any) {
        set({ error: e.message });
        return false;
      }
    },

    retimeActiveJob: async () => {
      const { activeJob, timingBusy } = get();
      if (!activeJob || timingBusy) return;
      const jobId = activeJob.id;
      const before = activeJob.segments
        .filter((s) => s.id)
        .map((s) => ({ id: s.id!, start_time: s.start_time, end_time: s.end_time }));
      set({ timingBusy: true, timingNote: null });
      try {
        const res = await api.retimeJob(jobId);
        if (get().activeJob?.id !== jobId) return;
        patchActiveJob({ segments: res.segments });
        set({
          timingNote: String(res.changed),
          timingUndo: res.changed > 0 ? { jobId, times: before } : null,
        });
      } catch (e: any) {
        set({ error: e.message, timingNote: null });
      } finally {
        set({ timingBusy: false });
      }
    },

    undoRetime: async () => {
      const { timingUndo, activeJob } = get();
      if (!timingUndo || !activeJob || activeJob.id !== timingUndo.jobId) return;
      set({ timingBusy: true });
      try {
        const saved = await api.updateSegments(timingUndo.jobId, timingUndo.times);
        const byId = new Map(saved.map((s) => [s.id, s]));
        const current = get().activeJob;
        if (current && current.id === timingUndo.jobId) {
          patchActiveJob({
            segments: current.segments.map((s) => (s.id && byId.has(s.id) ? { ...s, ...byId.get(s.id)! } : s)),
          });
        }
        set({ timingUndo: null, timingNote: null });
      } catch (e: any) {
        set({ error: e.message });
      } finally {
        set({ timingBusy: false });
      }
    },

    moveSegment: async (segmentId, toPosition) => {
      const { activeJob, timingBusy } = get();
      if (!activeJob || timingBusy) return;
      const jobId = activeJob.id;
      set({ timingBusy: true, timingNote: null, timingUndo: null });
      try {
        const res = await api.moveSegment(jobId, segmentId, toPosition);
        if (get().activeJob?.id !== jobId) return;
        patchActiveJob({ segments: res.segments });
        if (res.message) set({ error: res.message });
      } catch (e: any) {
        set({ error: e.message });
        get().refreshActiveJob();
      } finally {
        set({ timingBusy: false });
      }
    },

    replaceAll: async (search, replacement, column) => {
      const { activeJob } = get();
      if (!activeJob || !search) return 0;
      const pattern = new RegExp(escapeRegExp(search), 'gi');
      const field = column === 'translated' ? 'translated_text' : 'original_text';
      const patches = activeJob.segments
        .filter((s) => s.id && (s[field] || '').match(pattern))
        .map((s) => ({ id: s.id!, [field]: (s[field] || '').replace(pattern, replacement) }));
      if (patches.length === 0) return 0;
      try {
        const saved = await api.updateSegments(activeJob.id, patches);
        const byId = new Map(saved.map((s) => [s.id, s]));
        patchActiveJob({
          segments: activeJob.segments.map((s) => (s.id && byId.has(s.id) ? { ...s, ...byId.get(s.id)! } : s)),
        });
        return patches.length;
      } catch (e: any) {
        set({ error: e.message });
        return 0;
      }
    },

    setVideoCurrentTime: (time) => set({ videoCurrentTime: time }),

    setSelectedSegmentIndex: (index) => set({ selectedSegmentIndex: index }),

    seekTo: (time, play = false) =>
      set({ seekRequest: { time: Math.max(0, time), nonce: Date.now() + Math.random(), play } }),

    setSubtitleOverlayMode: (mode) => set({ subtitleOverlayMode: mode }),

    setApplyRtlExport: (val) => set({ applyRtlExport: val }),

    clearError: () => set({ error: null }),

    connectWebSocket: (jobId: string, isReconnect = false) => {
      const attempts = reconnectAttempts;
      get().disconnectWebSocket();
      reconnectAttempts = isReconnect ? attempts : 0;

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws/jobs/${jobId}`);
      activeSocket = socket;

      socket.onopen = () => {
        reconnectAttempts = 0;
        set({ wsConnected: true });
        // Catch up on anything missed while disconnected
        if (isReconnect) get().refreshActiveJob();
      };

      socket.onmessage = (event) => {
        try {
          handleMessage(JSON.parse(event.data));
        } catch (err) {
          console.error('Failed to handle WS message:', err);
        }
      };

      socket.onclose = () => {
        if (activeSocket !== socket) return; // replaced or closed on purpose
        activeSocket = null;
        set({ wsConnected: false });
        const job = get().activeJob;
        if (job && job.id === jobId && isActiveStatus(job.status)) {
          const delay = Math.min(10000, 1000 * 2 ** reconnectAttempts++);
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (get().activeJob?.id === jobId) get().connectWebSocket(jobId, true);
          }, delay);
        }
      };
    },

    disconnectWebSocket: () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      reconnectAttempts = 0;
      if (activeSocket) {
        const socket = activeSocket;
        activeSocket = null;
        socket.close();
      }
      set({ wsConnected: false });
    },
  };
});

import type { ParsedSubtitles, SegmentCreate, SyncCue, SystemInfo, ProbeResponse, Job, JobConfig, SubtitleSegment, SegmentPatch, SegmentsChange, QAIssue, SpeakerGuess, StorageReport, StorageCleanResult, Glossary, RetranslateOptions, AppSettings, SettingsResponse, CostEstimate, ModelCheckResponse } from '../types';

const API_BASE = '/api';

async function request<T>(url: string, init?: RequestInit, fallbackError = 'Request failed'): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: fallbackError }));
    throw new Error(typeof err.detail === 'string' ? err.detail : fallbackError);
  }
  return res.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  getSystemInfo(): Promise<SystemInfo> {
    return request(`${API_BASE}/system/info`, undefined, 'Failed to load system info');
  },

  probePath(filePath: string): Promise<ProbeResponse> {
    return request(`${API_BASE}/media/probe-path`, json('POST', { file_path: filePath }), 'Failed to inspect file path');
  },

  /** Returns the probe of an identical earlier upload (same name + size), or null. */
  findExistingUpload(file: File): Promise<ProbeResponse | null> {
    const params = new URLSearchParams({ name: file.name, size: String(file.size) });
    return request<ProbeResponse | null>(`${API_BASE}/media/upload/existing?${params}`).catch(() => null);
  },

  /** Upload with progress reporting (fetch() can't report upload progress). */
  uploadFile(file: File, onProgress?: (fraction: number) => void): Promise<ProbeResponse> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API_BASE}/media/upload`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        let body: any = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* ignore */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body?.detail || 'Upload failed'));
      };
      xhr.onerror = () => reject(new Error('Upload failed (network error)'));
      const formData = new FormData();
      formData.append('file', file);
      xhr.send(formData);
    });
  },

  /** Parse a subtitle file the user wants synced (SRT / WebVTT / ASS; any encoding). Nothing is stored. */
  parseSubtitles(file: File): Promise<ParsedSubtitles> {
    const formData = new FormData();
    formData.append('file', file);
    return request(`${API_BASE}/media/parse-subtitles`, { method: 'POST', body: formData }, 'Could not read the subtitle file');
  },

  createJob(
    params: JobConfig & {
      video_path: string;
      file_name?: string;
      sync_cues?: SyncCue[];
      sync_file_name?: string;
      sync_language?: string;
      sync_use_gemini?: boolean;
    }
  ): Promise<Job> {
    return request(`${API_BASE}/jobs`, json('POST', params), 'Failed to create job');
  },

  listJobs(): Promise<Job[]> {
    return request(`${API_BASE}/jobs`, undefined, 'Failed to list jobs');
  },

  getJob(jobId: string): Promise<Job> {
    return request(`${API_BASE}/jobs/${jobId}`, undefined, `Failed to load job ${jobId}`);
  },

  cancelJob(jobId: string): Promise<unknown> {
    return request(`${API_BASE}/jobs/${jobId}/cancel`, { method: 'POST' }, 'Failed to cancel job');
  },

  retranslate(jobId: string, options: RetranslateOptions): Promise<Job> {
    return request(`${API_BASE}/jobs/${jobId}/translate`, json('POST', options), 'Failed to start translation');
  },

  deleteJob(jobId: string): Promise<unknown> {
    return request(`${API_BASE}/jobs/${jobId}`, { method: 'DELETE' }, 'Failed to delete job');
  },

  updateSegment(jobId: string, segmentId: number, patch: SegmentPatch): Promise<SubtitleSegment> {
    return request(
      `${API_BASE}/jobs/${jobId}/segments/${segmentId}`,
      json('PATCH', patch),
      'Failed to update subtitle segment'
    );
  },

  updateSegments(jobId: string, patches: (SegmentPatch & { id: number })[]): Promise<SubtitleSegment[]> {
    return request(`${API_BASE}/jobs/${jobId}/segments`, json('PATCH', patches), 'Failed to update subtitles');
  },

  saveGlossary(jobId: string, glossary: Glossary, profile?: string): Promise<Glossary> {
    return request(
      `${API_BASE}/jobs/${jobId}/glossary`,
      json('PUT', profile === undefined ? glossary : { ...glossary, profile }),
      'Could not save the glossary'
    );
  },

  listGlossaryProfiles(): Promise<string[]> {
    return request(`${API_BASE}/glossary-profiles`, undefined, 'Could not load the series list');
  },

  storageReport(olderThanDays: number): Promise<StorageReport> {
    return request(`${API_BASE}/storage?older_than_days=${olderThanDays}`, undefined, 'Could not read storage usage');
  },

  storageClean(categories: string[], olderThanDays?: number): Promise<StorageCleanResult> {
    return request(
      `${API_BASE}/storage/clean`,
      json('POST', { categories, older_than_days: olderThanDays }),
      'Cleanup failed',
    );
  },

  detectSpeakers(
    jobId: string,
    segmentIds: number[],
    apiKey?: string,
    model?: string,
    thinking?: string,
  ): Promise<SpeakerGuess> {
    return request(
      `${API_BASE}/jobs/${jobId}/detect-speakers`,
      json('POST', {
        segment_ids: segmentIds,
        gemini_api_key_override: apiKey || undefined,
        gemini_model: model || undefined,
        thinking: thinking || undefined,
      }),
      'Could not detect speakers from the video',
    );
  },

  qualityCheck(jobId: string): Promise<QAIssue[]> {
    return request(`${API_BASE}/jobs/${jobId}/qa`, undefined, 'Quality check failed');
  },

  retimeJob(jobId: string): Promise<SegmentsChange> {
    return request(`${API_BASE}/jobs/${jobId}/retime`, json('POST', {}), 'Timing check failed');
  },

  /** Add a subtitle line by hand (it must not overlap an existing line). */
  createSegment(jobId: string, body: SegmentCreate): Promise<SegmentsChange> {
    return request(`${API_BASE}/jobs/${jobId}/segments`, json('POST', body), 'Could not add the line');
  },

  /** Delete a subtitle line by user request. */
  deleteSegment(jobId: string, segmentId: number): Promise<SegmentsChange> {
    return request(`${API_BASE}/jobs/${jobId}/segments/${segmentId}`, { method: 'DELETE' }, 'Could not delete the line');
  },

  moveSegment(jobId: string, segmentId: number, toPosition: number): Promise<SegmentsChange> {
    return request(
      `${API_BASE}/jobs/${jobId}/segments/move`,
      json('POST', { segment_id: segmentId, to_position: toPosition }),
      'Could not move subtitle'
    );
  },

  getSettings(): Promise<SettingsResponse> {
    return request(`${API_BASE}/settings`, undefined, 'Could not load settings');
  },

  saveSettings(settings: AppSettings): Promise<SettingsResponse> {
    return request(`${API_BASE}/settings`, json('PUT', settings), 'Could not save settings');
  },

  getApiKeyStatus(): Promise<import('../types').ApiKeyStatus> {
    return request(`${API_BASE}/settings/api-key`, undefined, 'Could not check API key status');
  },

  saveApiKey(apiKey: string): Promise<import('../types').ApiKeyStatus> {
    return request(`${API_BASE}/settings/api-key`, json('PUT', { api_key: apiKey }), 'Could not save API key');
  },

  estimateCost(params: {
    duration_seconds: number;
    enable_translation: boolean;
    detect_onscreen_text: boolean;
    has_forced_track?: boolean;
    sync_lines?: number;
    cost_saver_mode?: boolean;
    settings?: AppSettings;
  }): Promise<CostEstimate> {
    return request(`${API_BASE}/settings/estimate`, json('POST', params), 'Could not estimate cost');
  },

  checkModels(settings: AppSettings, apiKey?: string): Promise<ModelCheckResponse> {
    return request(
      `${API_BASE}/settings/check`,
      json('POST', { settings, api_key: apiKey || undefined }),
      'Could not test the models'
    );
  },

  getOllamaStatus(): Promise<import('../types').OllamaStatus> {
    return request(`${API_BASE}/settings/ollama/status`, undefined, 'Could not check local Ollama status');
  },

  setupOllama(model?: string): Promise<import('../types').OllamaStatus> {
    return request(
      `${API_BASE}/settings/ollama/setup`,
      json('POST', model ? { model } : {}),
      'Could not install or start Ollama'
    );
  },

  getVideoStreamUrl(jobId: string): string {
    return `${API_BASE}/media/stream/${jobId}`;
  },

  getExportUrl(jobId: string, format: 'srt' | 'vtt', useTranslated: boolean, applyRtl = true): string {
    return `${API_BASE}/jobs/${jobId}/export/${format}?use_translated=${useTranslated}&apply_rtl=${applyRtl}`;
  },

  /** Detailed log of everything that happened while the job ran (summary + log lines). */
  getJobLogUrl(jobId: string): string {
    return `${API_BASE}/jobs/${jobId}/export/log`;
  },

  /** The backend's own log file. */
  getServerLogUrl(): string {
    return `${API_BASE}/system/log`;
  },
};

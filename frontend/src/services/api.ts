import type { SystemInfo, ProbeResponse, Job, JobConfig, SubtitleSegment } from '../types';

const API_BASE = '/api';

export const api = {
  async getSystemInfo(): Promise<SystemInfo> {
    const res = await fetch(`${API_BASE}/system/info`);
    if (!res.ok) throw new Error('Failed to load system info');
    return res.json();
  },

  async probePath(filePath: string): Promise<ProbeResponse> {
    const res = await fetch(`${API_BASE}/media/probe-path`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to inspect file path' }));
      throw new Error(err.detail || 'Failed to inspect file path');
    }
    return res.json();
  },

  async uploadFile(file: File): Promise<ProbeResponse> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/media/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(err.detail || 'Upload failed');
    }
    return res.json();
  },

  async createJob(params: JobConfig & { video_path: string; file_name?: string }): Promise<Job> {
    const res = await fetch(`${API_BASE}/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to create job' }));
      throw new Error(err.detail || 'Failed to create job');
    }
    return res.json();
  },

  async listJobs(): Promise<Job[]> {
    const res = await fetch(`${API_BASE}/jobs`);
    if (!res.ok) throw new Error('Failed to list jobs');
    return res.json();
  },

  async getJob(jobId: string): Promise<Job> {
    const res = await fetch(`${API_BASE}/jobs/${jobId}`);
    if (!res.ok) throw new Error(`Failed to load job ${jobId}`);
    return res.json();
  },

  async cancelJob(jobId: string): Promise<any> {
    const res = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to cancel job');
    return res.json();
  },

  async deleteJob(jobId: string): Promise<any> {
    const res = await fetch(`${API_BASE}/jobs/${jobId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete job');
    return res.json();
  },

  async updateSegment(
    jobId: string,
    segmentId: number,
    patch: Partial<SubtitleSegment>
  ): Promise<SubtitleSegment> {
    const res = await fetch(`${API_BASE}/jobs/${jobId}/segments/${segmentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error('Failed to update subtitle segment');
    return res.json();
  },

  getVideoStreamUrl(jobId: string): string {
    return `${API_BASE}/media/stream/${jobId}`;
  },

  getExportSrtUrl(jobId: string, useTranslated: boolean, applyRtl: boolean = true): string {
    return `${API_BASE}/jobs/${jobId}/export/srt?use_translated=${useTranslated}&apply_rtl=${applyRtl}`;
  },

  getExportVttUrl(jobId: string, useTranslated: boolean, applyRtl: boolean = true): string {
    return `${API_BASE}/jobs/${jobId}/export/vtt?use_translated=${useTranslated}&apply_rtl=${applyRtl}`;
  },
};

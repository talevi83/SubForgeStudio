export type JobStatus =
  | 'IDLE'
  | 'PROBING'
  | 'EXTRACTING_AUDIO'
  | 'TRANSCRIBING'
  | 'REFINING'
  | 'TRANSLATING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

export interface AudioStreamInfo {
  index: number;
  codec_name: string;
  channels: number;
  channel_layout?: string;
  sample_rate?: string;
  language?: string;
  title?: string;
}

export interface VideoStreamInfo {
  codec_name: string;
  width?: number;
  height?: number;
  fps?: string;
  duration?: string;
}

export interface ProbeResponse {
  file_path: string;
  file_name: string;
  file_size: number;
  duration: number;
  format_name: string;
  video_streams: VideoStreamInfo[];
  audio_streams: AudioStreamInfo[];
}

export interface SubtitleSegment {
  id?: number;
  job_id: string;
  index: number;
  start_time: number;
  end_time: number;
  original_text: string;
  translated_text?: string | null;
  confidence?: number;
  cps?: number;
}

export interface Job {
  id: string;
  video_path: string;
  file_name: string;
  file_size?: number;
  status: JobStatus;
  current_stage?: string | null;
  progress_percent: number;
  speed_factor?: number | null;
  eta_seconds?: number | null;
  error_message?: string | null;
  video_duration?: number | null;
  audio_streams: AudioStreamInfo[];
  selected_audio_stream: number;
  whisper_model: string;
  source_language?: string | null;
  vad_filter: boolean;
  enable_translation: boolean;
  target_language: string;
  gemini_model: string;
  context_notes?: string | null;
  segments: SubtitleSegment[];
  created_at: string;
  updated_at: string;
}

export interface WebSocketEventPayload {
  event: 'STAGE_PROGRESS' | 'SEGMENT_CREATED' | 'BATCH_TRANSLATED' | 'JOB_COMPLETED' | 'JOB_FAILED';
  job_id: string;
  stage?: string;
  progress_percent: number;
  speed_factor?: number | null;
  eta_seconds?: number | null;
  segment?: SubtitleSegment | null;
  segments_batch?: SubtitleSegment[] | null;
  message?: string | null;
}

export interface HardwareInfo {
  cuda_available: boolean;
  device_count: number;
  gpu_name?: string | null;
  vram_total_mb?: number | null;
  vram_free_mb?: number | null;
  cpu_count: number;
  recommended_device: string;
  recommended_compute_type: string;
}

export interface SystemInfo {
  is_gemini_configured: boolean;
  gemini_model: string;
  whisper_default_model: string;
  hardware: HardwareInfo;
}

export interface JobConfig {
  selected_audio_stream: number;
  whisper_model: string;
  source_language: string;
  vad_filter: boolean;
  enable_translation: boolean;
  target_language: string;
  gemini_model: string;
  gemini_api_key_override?: string;
  context_notes?: string;
}

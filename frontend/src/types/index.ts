export type JobStatus =
  | 'IDLE'
  | 'QUEUED'
  | 'PROBING'
  | 'EXTRACTING_AUDIO'
  | 'TRANSCRIBING'
  | 'SCANNING_ONSCREEN'
  | 'REFINING'
  | 'TRANSLATING'
  | 'SYNCING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

export const ACTIVE_STATUSES: JobStatus[] = [
  'IDLE',
  'QUEUED',
  'PROBING',
  'EXTRACTING_AUDIO',
  'TRANSCRIBING',
  'SCANNING_ONSCREEN',
  'REFINING',
  'TRANSLATING',
  'SYNCING',
];

export const isActiveStatus = (status?: JobStatus | null) =>
  !!status && ACTIVE_STATUSES.includes(status);

export interface AudioStreamInfo {
  index: number;
  codec_name: string;
  channels: number;
  channel_layout?: string;
  sample_rate?: string;
  language?: string;
  title?: string;
}

export interface SubtitleStreamInfo {
  index: number;
  codec_name: string;
  language?: string;
  title?: string | null;
  forced: boolean;
  default: boolean;
  text_based: boolean;
}

export type SegmentType = 'DIALOGUE' | 'COMMS' | 'ON_SCREEN' | 'FOREIGN';
export type OnscreenPosition = 'top_center' | 'top_left' | 'top_right' | 'middle_left' | 'middle_right';

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
  is_upload?: boolean;
  video_streams: VideoStreamInfo[];
  audio_streams: AudioStreamInfo[];
  subtitle_streams?: SubtitleStreamInfo[];
}

export interface SubtitleSegment {
  id?: number;
  job_id: string;
  index: number;
  start_time: number;
  end_time: number;
  original_text: string;
  translated_text?: string | null;
  translation_failed?: boolean;
  segment_type?: SegmentType;
  confidence?: number;
  cps?: number;
}

export interface GlossaryCharacter {
  name: string;
  target_name?: string;
  gender: string;
  notes?: string;
}

export interface Glossary {
  summary?: string;
  characters?: GlossaryCharacter[];
  terms?: { source: string; translation: string; notes?: string }[];
  register_notes?: string;
}

export interface Job {
  id: string;
  video_path: string;
  file_name: string;
  file_size?: number;
  is_upload?: boolean;
  status: JobStatus;
  current_stage?: string | null;
  progress_percent: number;
  speed_factor?: number | null;
  eta_seconds?: number | null;
  error_message?: string | null;
  warning_message?: string | null;
  video_duration?: number | null;
  audio_streams: AudioStreamInfo[];
  selected_audio_stream: number;
  whisper_model: string;
  source_language?: string | null;
  detected_language?: string | null;
  vad_filter: boolean;
  comms_boost: boolean;
  high_speech_sensitivity?: boolean;
  use_embedded_subtitles?: boolean;
  detect_onscreen_text: boolean;
  onscreen_sensitivity?: number;
  allow_onscreen_overlap?: boolean;
  onscreen_position?: OnscreenPosition;
  convert_to_metric?: boolean;
  enable_translation: boolean;
  target_language: string;
  gemini_model: string;
  context_notes?: string | null;
  glossary_profile?: string | null;
  glossary?: Glossary | null;
  usage?: JobUsage | null;
  segment_count: number;
  failed_translation_count: number;
  /** 'sync' = the user's own subtitle file re-timed to the video */
  job_kind?: 'transcribe' | 'sync';
  sync_file_name?: string | null;
  sync_report?: SyncReport | null;
  segments: SubtitleSegment[];
  created_at: string;
  updated_at: string;
}

export interface WebSocketEventPayload {
  event:
    | 'STAGE_PROGRESS'
    | 'SEGMENT_CREATED'
    | 'SEGMENTS_REFINED'
    | 'BATCH_TRANSLATED'
    | 'JOB_COMPLETED'
    | 'JOB_FAILED'
    | 'JOB_CANCELLED';
  job_id: string;
  stage?: string;
  progress_percent: number;
  speed_factor?: number | null;
  eta_seconds?: number | null;
  segment?: SubtitleSegment | null;
  segments_batch?: SubtitleSegment[] | null;
  message?: string | null;
  usage?: JobUsage | null;
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
  gemini_models: string[];
  whisper_default_model: string;
  whisper_models: string[];
  max_cps_warning: number;
  hardware: HardwareInfo;
}

/** One line of a subtitle file the user wants synced (as parsed by the server). */
export interface SyncCue {
  start: number;
  end: number;
  text: string;
  /** 'top' = placed at the top of the picture in the file (signs, titles) */
  position: 'top' | 'bottom';
  /** 'sound' = a hearing-impaired description such as [door slams] */
  kind: 'speech' | 'sound';
}

export interface ParsedSubtitles {
  file_name: string;
  format: string;
  encoding: string;
  language: string;
  cue_count: number;
  first_start: number;
  last_end: number;
  top_count: number;
  sound_count: number;
  cues: SyncCue[];
}

export type SyncMethod = 'text' | 'gemini' | 'screen' | 'burned_in' | 'embedded' | 'speech';

export interface SyncReport {
  file_name?: string;
  language?: string;
  spoken_language?: string;
  lines?: number;
  matching?: string;
  frame_rate?: string;
  offset?: number;
  jump_count?: number;
  reference?: { source: 'embedded' | 'burned_in' | null; detail?: string };
  methods?: Partial<Record<SyncMethod | 'low_confidence', number>>;
}

export type IngestMode = 'transcribe' | 'sync';

export interface JobConfig {
  selected_audio_stream: number;
  whisper_model: string;
  source_language: string;
  vad_filter: boolean;
  comms_boost: boolean;
  high_speech_sensitivity?: boolean;
  use_embedded_subtitles?: boolean;
  detect_onscreen_text: boolean;
  onscreen_sensitivity?: number;
  allow_onscreen_overlap?: boolean;
  onscreen_position?: OnscreenPosition;
  convert_to_metric?: boolean;
  enable_translation: boolean;
  target_language: string;
  gemini_api_key_override?: string;
  context_notes?: string;
  /** Series glossary shared between episodes (saved by name) */
  glossary_profile?: string;
}

export interface RetranslateOptions {
  only_failed?: boolean;
  target_language?: string;
  gemini_model?: string;
  context_notes?: string;
  gemini_api_key_override?: string;
  rebuild_glossary?: boolean;
  /** Translate only these rows (segment ids), optionally with a note about who speaks to whom */
  segment_ids?: number[];
  thinking?: ThinkingLevel;
  speaker_gender?: Gender | null;
  addressee_gender?: Gender | null;
  hint?: string;
  remember_hint?: boolean;
}

export type Gender = 'male' | 'female' | 'group';

export type SegmentPatch = Partial<
  Pick<SubtitleSegment, 'start_time' | 'end_time' | 'original_text' | 'translated_text'>
>;

export type StorageCategoryId = 'temp' | 'cache' | 'logs' | 'exports' | 'uploads' | 'jobs';

export interface StorageReport {
  categories: { id: StorageCategoryId; bytes: number; count: number }[];
  database_bytes: number;
}

export interface StorageCleanResult {
  freed_bytes: number;
  removed: Record<string, { bytes: number; count: number }>;
}

export interface SpeakerGuess {
  speaker_gender: Gender | null;
  addressee_gender: Gender | null;
  explanation: string;
}

export interface QAIssue {
  segment_id: number;
  index: number;
  kind: 'latin' | 'name' | 'term' | 'agreement' | 'missing';
  message: string;
}

export interface SegmentsChange {
  changed: number;
  retimed: boolean;
  message?: string | null;
  /** The line POST /segments added */
  created_id?: number | null;
  segments: SubtitleSegment[];
}

/** A subtitle line the user adds by hand. */
export interface SegmentCreate {
  start_time: number;
  end_time: number;
  original_text: string;
  translated_text?: string | null;
  segment_type?: SegmentType;
}

/** Where the "add a line" dialog opens: at a time (the playhead), or right after a line. */
export interface InsertDraft {
  time: number;
  afterId?: number;
}

export const RTL_LANGUAGES = ['hebrew', 'arabic', 'persian', 'farsi', 'urdu', 'yiddish', 'he', 'ar', 'fa', 'ur', 'yi'];
export const isRtlLanguage = (lang?: string | null) =>
  !!lang && RTL_LANGUAGES.includes(lang.trim().toLowerCase());

// ---------------------------------------------------------------------------
// Settings, model catalog, pricing
// ---------------------------------------------------------------------------

export type ThinkingLevel = 'default' | 'off' | 'minimal' | 'low' | 'medium' | 'high';
export type MediaResolution = 'low' | 'medium' | 'high';

export interface GeminiCallSettings {
  model: string;
  thinking: ThinkingLevel;
  /** Flex service tier: half price, slower, falls back to Standard when Google is busy */
  flex?: boolean;
}

export interface AppSettings {
  translation: GeminiCallSettings;
  glossary: GeminiCallSettings;
  vision: GeminiCallSettings;
  vision_media_resolution: MediaResolution;
  onscreen_position?: OnscreenPosition;
  convert_to_metric?: boolean;
  cost_saver_mode?: boolean;
}

export interface CatalogModel {
  id: string;
  label: string;
  family: string;
  preview: boolean;
  thinking: ThinkingLevel[];
  default_level: string;
  input_per_m: number;
  output_per_m: number;
  input_long_per_m?: number | null;
  output_long_per_m?: number | null;
  price_change?: { after: string; input_per_m: number; output_per_m: number } | null;
  supports_media_resolution: boolean;
  /** Pipeline steps this model is offered for */
  tasks: PipelineTask[];
  supports_flex?: boolean;
  /** Share of the Standard price billed on Flex (0.5), or null when Flex isn't offered */
  flex_price_factor?: number | null;
  note?: string;
}

export type PipelineTask = 'translation' | 'glossary' | 'vision';

export interface ModelCheckResult {
  ok: boolean;
  error: string | null;
}

export interface ModelCheckResponse {
  /** Keyed by model id */
  results: Record<string, ModelCheckResult>;
  /** Keyed by pipeline step (the vision probe also sends an image) */
  steps?: Partial<Record<PipelineTask, ModelCheckResult & { model: string }>>;
}

export interface SettingsResponse {
  settings: AppSettings;
  catalog: CatalogModel[];
  pricing_source: string;
  pricing_checked: string;
}

export interface OllamaStatus {
  installed: boolean;
  running: boolean;
  models: string[];
  selected_model: string | null;
  recommended_model: string;
  download_url: string;
  installing: boolean;
  step: string | null;
  error: string | null;
}

export type GeminiStage = 'glossary' | 'translation' | 'onscreen' | 'foreign';

export interface StageEstimate {
  model: string;
  thinking: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  cost_usd: number | null;
  flex?: boolean;
  frames?: number;
}

export interface CostEstimate {
  duration_seconds: number;
  segments: number;
  stages: Partial<Record<GeminiStage, StageEstimate>>;
  total_cost_usd: number | null;
}

export interface StageUsage {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  cost_usd: number | null;
  flex_requests?: number;
  flex_fallbacks?: number;
}

export interface ModelUsage extends StageUsage {
  total_tokens: number;
}

export interface LocalUsage {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens?: number;
  segments_tagged: number;
  frames_filtered: number;
  saved_tokens: number;
  saved_cost_usd: number;
}

export interface JobUsage {
  stages: Partial<Record<GeminiStage | 'sync', StageUsage>>;
  models?: Record<string, ModelUsage>;
  local?: LocalUsage | null;
  total_input_tokens?: number;
  total_output_tokens?: number;
  total_thinking_tokens?: number;
  total_tokens?: number;
  total_cost_usd: number | null;
}

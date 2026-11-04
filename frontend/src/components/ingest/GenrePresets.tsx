import React from 'react';
import type { JobConfig } from '../../types';
import { useT } from '../../i18n';
import type { MessageKey } from '../../i18n';
import { Wand2, Check } from 'lucide-react';

interface GenrePresetDefinition {
  id: string;
  icon: string;
  titleKey: MessageKey;
  descKey: MessageKey;
  preferredWhisperModel: string;
  tags: string[];
  config: Partial<JobConfig>;
}

const GENRE_PRESETS: GenrePresetDefinition[] = [
  {
    id: 'drama',
    icon: '🎬',
    titleKey: 'config.presetDramaTitle',
    descKey: 'config.presetDramaDesc',
    preferredWhisperModel: 'large-v3-turbo',
    tags: ['OCR · 1', 'SDH', 'Metric'],
    config: {
      vad_filter: true,
      high_speech_sensitivity: false,
      use_embedded_subtitles: true,
      supplement_with_whisper: false,
      comms_boost: false,
      detect_onscreen_text: true,
      onscreen_sensitivity: 1,
      allow_onscreen_overlap: true,
      onscreen_position: 'top_center',
      convert_to_metric: true,
      remove_sdh_speakers: true,
      enable_translation: true,
    },
  },
  {
    id: 'docu',
    icon: '🔬',
    titleKey: 'config.presetDocuTitle',
    descKey: 'config.presetDocuDesc',
    preferredWhisperModel: 'large-v3-turbo',
    tags: ['OCR · 2', '+Whisper', 'Comms', 'Metric'],
    config: {
      vad_filter: true,
      high_speech_sensitivity: false,
      use_embedded_subtitles: true,
      supplement_with_whisper: true,
      comms_boost: true,
      detect_onscreen_text: true,
      onscreen_sensitivity: 2,
      allow_onscreen_overlap: true,
      onscreen_position: 'top_center',
      convert_to_metric: true,
      remove_sdh_speakers: true,
      enable_translation: true,
    },
  },
  {
    id: 'action',
    icon: '🚁',
    titleKey: 'config.presetActionTitle',
    descKey: 'config.presetActionDesc',
    preferredWhisperModel: 'large-v3-turbo',
    tags: ['Comms', 'High Sens', '+Whisper', 'OCR · 2'],
    config: {
      vad_filter: true,
      high_speech_sensitivity: true,
      use_embedded_subtitles: true,
      supplement_with_whisper: true,
      comms_boost: true,
      detect_onscreen_text: true,
      onscreen_sensitivity: 2,
      allow_onscreen_overlap: true,
      onscreen_position: 'top_center',
      convert_to_metric: true,
      remove_sdh_speakers: true,
      enable_translation: true,
    },
  },
  {
    id: 'thriller',
    icon: '🕵️',
    titleKey: 'config.presetThrillerTitle',
    descKey: 'config.presetThrillerDesc',
    preferredWhisperModel: 'large-v3',
    tags: ['High Sens', 'OCR · 3', '+Whisper'],
    config: {
      vad_filter: true,
      high_speech_sensitivity: true,
      use_embedded_subtitles: true,
      supplement_with_whisper: true,
      comms_boost: false,
      detect_onscreen_text: true,
      onscreen_sensitivity: 3,
      allow_onscreen_overlap: true,
      onscreen_position: 'top_center',
      convert_to_metric: true,
      remove_sdh_speakers: true,
      enable_translation: true,
    },
  },
  {
    id: 'scifi',
    icon: '🚀',
    titleKey: 'config.presetScifiTitle',
    descKey: 'config.presetScifiDesc',
    preferredWhisperModel: 'large-v3-turbo',
    tags: ['OCR · 3', 'Comms', 'High Sens', '+Whisper'],
    config: {
      vad_filter: true,
      high_speech_sensitivity: true,
      use_embedded_subtitles: true,
      supplement_with_whisper: true,
      comms_boost: true,
      detect_onscreen_text: true,
      onscreen_sensitivity: 3,
      allow_onscreen_overlap: true,
      onscreen_position: 'top_center',
      convert_to_metric: false,
      remove_sdh_speakers: true,
      enable_translation: true,
    },
  },
  {
    id: 'fast',
    icon: '⚡',
    titleKey: 'config.presetFastTitle',
    descKey: 'config.presetFastDesc',
    preferredWhisperModel: 'large-v3-turbo',
    tags: ['No OCR', 'SDH', 'Metric'],
    config: {
      vad_filter: true,
      high_speech_sensitivity: false,
      use_embedded_subtitles: true,
      supplement_with_whisper: false,
      comms_boost: false,
      detect_onscreen_text: false,
      onscreen_sensitivity: 1,
      allow_onscreen_overlap: true,
      onscreen_position: 'top_center',
      convert_to_metric: true,
      remove_sdh_speakers: true,
      enable_translation: true,
    },
  },
];

function resolveModel(preferred: string, available: string[]): string {
  if (available.includes(preferred)) return preferred;
  if (available.includes('large-v3-turbo')) return 'large-v3-turbo';
  if (available.includes('medium')) return 'medium';
  return available[0] || preferred;
}

function matchesPreset(current: JobConfig, preset: GenrePresetDefinition, availableModels: string[]): boolean {
  const c = preset.config;
  const expectedModel = resolveModel(preset.preferredWhisperModel, availableModels);
  if (current.whisper_model !== expectedModel) return false;
  if (Boolean(current.vad_filter) !== Boolean(c.vad_filter)) return false;
  if (Boolean(current.high_speech_sensitivity) !== Boolean(c.high_speech_sensitivity)) return false;
  if ((current.use_embedded_subtitles !== false) !== (c.use_embedded_subtitles !== false)) return false;
  if (Boolean(current.supplement_with_whisper) !== Boolean(c.supplement_with_whisper)) return false;
  if (Boolean(current.comms_boost) !== Boolean(c.comms_boost)) return false;
  if (Boolean(current.detect_onscreen_text) !== Boolean(c.detect_onscreen_text)) return false;
  if (c.detect_onscreen_text && (current.onscreen_sensitivity ?? 2) !== c.onscreen_sensitivity) return false;
  if (Boolean(current.convert_to_metric) !== Boolean(c.convert_to_metric)) return false;
  if (Boolean(current.remove_sdh_speakers) !== Boolean(c.remove_sdh_speakers)) return false;
  return true;
}

export const GenrePresets: React.FC<{
  pipelineConfig: JobConfig;
  whisperModels: string[];
  onApply: (patch: Partial<JobConfig>) => void;
}> = ({ pipelineConfig, whisperModels, onApply }) => {
  const t = useT();

  const activePreset = GENRE_PRESETS.find((p) => matchesPreset(pipelineConfig, p, whisperModels));

  const handleSelect = (preset: GenrePresetDefinition) => {
    const model = resolveModel(preset.preferredWhisperModel, whisperModels);
    onApply({
      ...preset.config,
      whisper_model: model,
    });
  };

  return (
    <div
      className="p-4 rounded-xl bg-gradient-to-b from-indigo-500/[0.07] to-sunken border border-indigo-500/25 space-y-3"
      data-testid="genre-presets"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2 text-xs font-semibold text-zinc-100">
            <Wand2 className="w-4 h-4 text-indigo-400" />
            <span>{t('config.presetsTitle')}</span>
          </div>
          <p className="text-[11px] text-zinc-400">{t('config.presetsSubtitle')}</p>
        </div>

        <span
          className={`text-[11px] font-medium px-2.5 py-1 rounded-full border transition-colors ${
            activePreset
              ? 'bg-indigo-500/15 border-indigo-500/40 text-indigo-300'
              : 'bg-zinc-800/80 border-line text-zinc-400'
          }`}
        >
          {activePreset
            ? t('config.presetsActiveBadge', { name: t(activePreset.titleKey) })
            : t('config.presetsCustomBadge')}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
        {GENRE_PRESETS.map((preset) => {
          const isActive = activePreset?.id === preset.id;
          return (
            <button
              key={preset.id}
              type="button"
              data-testid={`genre-preset-${preset.id}`}
              onClick={() => handleSelect(preset)}
              aria-pressed={isActive}
              className={`group relative p-3 rounded-xl border text-start flex flex-col justify-between gap-2 transition-all cursor-pointer ${
                isActive
                  ? 'bg-indigo-600/20 border-indigo-500/70 ring-1 ring-indigo-500/40 shadow-md shadow-indigo-950/40'
                  : 'bg-surface/90 border-line hover:border-indigo-500/40 hover:bg-surface'
              }`}
            >
              <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-lg leading-none" aria-hidden>
                    {preset.icon}
                  </span>
                  {isActive && (
                    <span className="w-4 h-4 rounded-full bg-indigo-500 text-white flex items-center justify-center shadow-sm">
                      <Check className="w-2.5 h-2.5 stroke-[3]" />
                    </span>
                  )}
                </div>
                <div className="text-xs font-semibold text-zinc-100 group-hover:text-white leading-snug">
                  {t(preset.titleKey)}
                </div>
                <p className="text-[10px] text-zinc-400 leading-relaxed line-clamp-2">
                  {t(preset.descKey)}
                </p>
              </div>

              <div className="flex flex-wrap gap-1 pt-1 border-t border-line/60">
                {preset.tags.map((tag) => (
                  <span
                    key={tag}
                    dir="ltr"
                    className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${
                      isActive
                        ? 'bg-indigo-500/25 text-indigo-200 border border-indigo-400/30'
                        : 'bg-sunken text-zinc-400 border border-line'
                    }`}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};

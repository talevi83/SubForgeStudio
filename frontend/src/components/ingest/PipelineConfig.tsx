import React, { useEffect, useState } from 'react';
import { api } from '../../services/api';
import { useJobStore } from '../../store/useJobStore';
import { useUiStore } from '../../store/useUiStore';
import { useSettingsStore } from '../../store/useSettingsStore';
import { useT, formatUsd } from '../../i18n';
import type { MessageKey } from '../../i18n';
import { useCostEstimate } from '../../hooks/useCostEstimate';
import { SOURCE_LANGUAGES, TARGET_LANGUAGES, sameLanguage } from './languages';
import { thinkingLabel, modelLabel } from '../settings/labels';
import { SyncOptions } from './SyncOptions';
import { AlertTriangle, Sparkles, Settings, Mic, Key, Play, Loader2, FileText, Coins, Users } from 'lucide-react';

/** Accessible on/off switch with a title and a one-line description. */
export const FeatureToggle: React.FC<{
  icon: string;
  title: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  note?: React.ReactNode;
}> = ({ icon, title, description, checked, onChange, note }) => (
  <label
    className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
      checked ? 'bg-indigo-500/10 border-indigo-500/40' : 'bg-surface border-line hover:border-zinc-600'
    }`}
  >
    <span className="text-lg leading-none mt-0.5" aria-hidden>
      {icon}
    </span>
    <span className="flex-1 min-w-0">
      <span className="block text-xs font-semibold text-zinc-200">{title}</span>
      <span className="block text-[11px] text-zinc-400 mt-0.5 leading-relaxed">{description}</span>
      {note && <span className="block text-[11px] mt-1">{note}</span>}
    </span>
    <input
      type="checkbox"
      role="switch"
      aria-checked={checked}
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      className="sr-only peer"
    />
    <span
      className={`relative mt-0.5 inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-500 ${
        checked ? 'bg-indigo-600' : 'bg-zinc-700'
      }`}
      aria-hidden
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${checked ? 'start-4.5' : 'start-0.5'}`}
      />
    </span>
  </label>
);

const selectClass =
  'w-full bg-surface border border-line rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:border-indigo-500';

export const PipelineConfig: React.FC = () => {
  const pipelineConfig = useJobStore((s) => s.pipelineConfig);
  const updatePipelineConfig = useJobStore((s) => s.updatePipelineConfig);
  const createAndStartJob = useJobStore((s) => s.createAndStartJob);
  const probeResult = useJobStore((s) => s.probeResult);
  const systemInfo = useJobStore((s) => s.systemInfo);
  const isLoading = useJobStore((s) => s.isLoading);
  const syncing = useJobStore((s) => s.ingestMode === 'sync');
  const syncFile = useJobStore((s) => s.syncFile);
  const syncLanguage = useJobStore((s) => s.syncLanguage);
  const syncUseGemini = useJobStore((s) => s.syncUseGemini);
  const [seriesProfiles, setSeriesProfiles] = useState<string[]>([]);
  useEffect(() => {
    api.listGlossaryProfiles().then(setSeriesProfiles).catch(() => setSeriesProfiles([]));
  }, []);
  const appSettings = useSettingsStore((s) => s.settings);
  const catalog = useSettingsStore((s) => s.catalog);
  const setView = useUiStore((s) => s.setView);
  const language = useUiStore((s) => s.language);
  const t = useT();

  const [showKeyOverride, setShowKeyOverride] = useState(false);
  const isGeminiReady = systemInfo?.is_gemini_configured;
  const whisperModels = systemInfo?.whisper_models?.length
    ? systemInfo.whisper_models
    : ['tiny', 'base', 'small', 'medium', 'large-v3-turbo', 'large-v3'];
  // Mirrors the backend's choice: a text-based forced/"signs" track beats scanning frames
  const forcedTrack = (probeResult?.subtitle_streams || []).find(
    (s) => s.text_based && (s.forced || /forced|signs/i.test(s.title || ''))
  );
  const translationWithoutKey =
    pipelineConfig.enable_translation && !isGeminiReady && !pipelineConfig.gemini_api_key_override;

  // Syncing a file in another language than the speech asks Gemini to match its lines
  const syncGeminiLines =
    syncing && syncFile && syncUseGemini && !sameLanguage(syncLanguage || syncFile.language, pipelineConfig.source_language)
      ? syncFile.cue_count
      : 0;
  const estimate = useCostEstimate(
    probeResult
      ? {
          duration_seconds: probeResult.duration,
          enable_translation: syncing ? false : pipelineConfig.enable_translation,
          detect_onscreen_text: pipelineConfig.detect_onscreen_text,
          has_forced_track: !!forcedTrack,
          sync_lines: syncGeminiLines,
          cost_saver_mode: appSettings?.cost_saver_mode,
          settings: appSettings,
        }
      : null
  );
  const cannotLaunch = syncing && !syncFile;

  if (!probeResult) return null;

  const translationModel = appSettings?.translation.model;
  const translationInfo = catalog.find((m) => m.id === translationModel);
  const estimateStages = estimate ? Object.entries(estimate.stages) : [];

  return (
    <div className="bg-surface border border-line rounded-xl p-5 shadow-lg space-y-6">
      <div className="flex items-center justify-between border-b border-line pb-3">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <Settings className="w-4 h-4 text-indigo-400" />
          {t('config.title')}
        </h2>
        <span className="text-xs text-zinc-400">{t('config.subtitle')}</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Whisper */}
        <div className="space-y-4 p-4 rounded-xl bg-sunken border border-line">
          <div className="flex items-center gap-2 text-xs font-semibold text-zinc-200 border-b border-line pb-2">
            <Mic className="w-4 h-4 text-indigo-400" />
            {t('config.whisperTitle')}
          </div>
          <div className="space-y-3 text-xs">
            <div>
              <label className="block text-zinc-400 mb-1 font-medium">{t('config.whisperModel')}</label>
              <select
                value={pipelineConfig.whisper_model}
                onChange={(e) => updatePipelineConfig({ whisper_model: e.target.value })}
                className={selectClass}
              >
                {whisperModels.map((m) => (
                  <option key={m} value={m}>
                    {m in { tiny: 1, base: 1, small: 1, medium: 1, 'large-v3-turbo': 1, 'large-v3': 1 }
                      ? t(`whisperModels.${m}` as MessageKey)
                      : m}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-zinc-400 mb-1 font-medium">{t('config.sourceLanguage')}</label>
              <select
                value={pipelineConfig.source_language}
                onChange={(e) => updatePipelineConfig({ source_language: e.target.value })}
                className={selectClass}
              >
                {SOURCE_LANGUAGES.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.value === 'auto' ? t('languages.auto') : l.label}
                  </option>
                ))}
              </select>
            </div>
            <label className="pt-1 flex items-center justify-between gap-3 cursor-pointer">
              <span>
                <span className="text-zinc-300 font-medium block">{t('config.vad')}</span>
                <span className="text-[11px] text-zinc-500 block">{t('config.vadHint')}</span>
              </span>
              <input
                type="checkbox"
                checked={pipelineConfig.vad_filter}
                onChange={(e) => updatePipelineConfig({ vad_filter: e.target.checked })}
                className="w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800 cursor-pointer"
              />
            </label>
          </div>
        </div>

        {/* Translation (or, when syncing a file, how its lines are matched) */}
        {syncing ? (
          <SyncOptions />
        ) : (
        <div className="space-y-4 p-4 rounded-xl bg-sunken border border-line">
          <div className="flex items-center justify-between border-b border-line pb-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-zinc-200">
              <Sparkles className="w-4 h-4 text-indigo-400" />
              {t('config.translationTitle')}
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <span className="text-[11px] text-zinc-400">{t('config.enable')}</span>
              <input
                type="checkbox"
                checked={pipelineConfig.enable_translation}
                onChange={(e) => updatePipelineConfig({ enable_translation: e.target.checked })}
                className="w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800 cursor-pointer"
              />
            </label>
          </div>

          {pipelineConfig.enable_translation ? (
            <div className="space-y-3 text-xs">
              <div>
                <label className="block text-zinc-400 mb-1 font-medium">{t('config.targetLanguage')}</label>
                <select
                  value={pipelineConfig.target_language}
                  onChange={(e) => updatePipelineConfig({ target_language: e.target.value })}
                  className={selectClass}
                >
                  {TARGET_LANGUAGES.map((l) => (
                    <option key={l.value} value={l.value}>
                      {l.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="p-2.5 rounded-lg bg-surface border border-line flex items-center justify-between gap-2">
                <span className="text-[11px] text-zinc-300 min-w-0 truncate">
                  {appSettings
                    ? t('config.modelSummary', {
                        model: modelLabel(catalog, appSettings.translation.model),
                        thinking: thinkingLabel(t, appSettings.translation.thinking, translationInfo),
                      }) + (appSettings.translation.flex ? ` · ${t('settings.flexTag')}` : '')
                    : t('config.models')}
                </span>
                <button
                  type="button"
                  onClick={() => setView('settings')}
                  className="text-[11px] text-indigo-400 hover:text-indigo-300 underline cursor-pointer whitespace-nowrap"
                >
                  {t('config.changeInSettings')}
                </button>
              </div>

              <div className="p-2.5 rounded-lg bg-surface border border-line flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-zinc-300">
                    <Key className="w-3.5 h-3.5 text-zinc-400" />
                    <span className="text-[11px]" dir="ltr">
                      {t('config.keyLabel')}
                    </span>
                    <span
                      className={`text-[10px] font-semibold px-1.5 rounded ${
                        isGeminiReady ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-400'
                      }`}
                    >
                      {isGeminiReady ? t('config.keyLoaded') : t('config.keyMissing')}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowKeyOverride(!showKeyOverride)}
                    className="text-[10px] text-indigo-400 hover:text-indigo-300 underline cursor-pointer"
                  >
                    {showKeyOverride ? t('config.hideOverride') : t('config.overrideKey')}
                  </button>
                </div>
                {translationWithoutKey && (
                  <div className="flex items-start gap-1.5 text-[11px] text-amber-300">
                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                    <span>{t('config.noKeyWarning')}</span>
                  </div>
                )}
                {(showKeyOverride || translationWithoutKey) && (
                  <input
                    type="password"
                    dir="ltr"
                    placeholder={t('config.keyPlaceholder')}
                    value={pipelineConfig.gemini_api_key_override || ''}
                    onChange={(e) => updatePipelineConfig({ gemini_api_key_override: e.target.value })}
                    className="w-full bg-sunken border border-line rounded px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 font-mono"
                  />
                )}
              </div>

              <div>
                <label className="text-zinc-400 mb-1 font-medium flex items-center gap-1.5">
                  <Users className="w-3.5 h-3.5 text-zinc-400" />
                  {t('config.series')}
                </label>
                <input
                  dir="auto"
                  list="series-profiles"
                  value={pipelineConfig.glossary_profile || ''}
                  onChange={(e) => updatePipelineConfig({ glossary_profile: e.target.value })}
                  placeholder={t('config.seriesPlaceholder')}
                  data-testid="series-profile"
                  className="w-full bg-surface border border-line rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500"
                />
                <datalist id="series-profiles">
                  {seriesProfiles.map((p) => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
                <p className="mt-1 text-[11px] text-zinc-500">{t('config.seriesHelp')}</p>
              </div>

              <div>
                <label className="text-zinc-400 mb-1 font-medium flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5 text-zinc-400" />
                  {t('config.notes')}
                </label>
                <textarea
                  rows={2}
                  dir="auto"
                  value={pipelineConfig.context_notes || ''}
                  onChange={(e) => updatePipelineConfig({ context_notes: e.target.value })}
                  placeholder={t('config.notesPlaceholder')}
                  className="w-full bg-surface border border-line rounded-lg p-2 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 resize-none"
                />
              </div>
            </div>
          ) : (
            <div className="py-6 text-center text-zinc-500 text-xs">{t('config.translationDisabled')}</div>
          )}
        </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <FeatureToggle
          icon="📻"
          title={t('config.commsTitle')}
          description={t('config.commsDescription')}
          checked={pipelineConfig.comms_boost}
          onChange={(v) => updatePipelineConfig({ comms_boost: v })}
        />
        <FeatureToggle
          icon="📺"
          title={syncing ? t('config.syncOnscreenTitle') : t('config.onscreenTitle')}
          description={syncing ? t('config.syncOnscreenDescription') : t('config.onscreenDescription')}
          checked={pipelineConfig.detect_onscreen_text}
          onChange={(v) => updatePipelineConfig({ detect_onscreen_text: v })}
          note={
            pipelineConfig.detect_onscreen_text &&
            (forcedTrack ? (
              <span className="text-emerald-400">
                {t('config.forcedFound', { name: forcedTrack.title || forcedTrack.language || `#${forcedTrack.index}` })}
              </span>
            ) : !isGeminiReady && !pipelineConfig.gemini_api_key_override ? (
              <span className="text-amber-300">{t('config.onscreenNeedsKey')}</span>
            ) : null)
          }
        />
      </div>

      {/* Cost estimate for this file */}
      {estimate && (
        <div className="p-3 rounded-lg bg-sunken border border-line text-xs flex items-start gap-3" data-testid="file-estimate">
          <Coins className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            {estimateStages.length === 0 ? (
              <span className="text-zinc-400">{t('config.estimateNone')}</span>
            ) : (
              <>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-zinc-300 font-medium">{t('config.estimateTitle')}</span>
                  <span className="text-zinc-50 font-semibold" dir="ltr">
                    ≈ {formatUsd(estimate.total_cost_usd, language)}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-zinc-500">
                  {estimateStages.map(([stage, s]) => (
                    <span key={stage}>
                      {t(`stages.${stage}` as MessageKey)}: {modelLabel(catalog, s!.model)}
                      {s!.flex ? ` (${t('settings.flexTag')})` : ''} ·{' '}
                      <span dir="ltr">{formatUsd(s!.cost_usd, language)}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <div className="pt-2 space-y-2">
        {cannotLaunch && <p className="text-center text-[11px] text-amber-300">{t('config.syncNeedFile')}</p>}
        <button
          onClick={createAndStartJob}
          disabled={isLoading || cannotLaunch}
          data-testid="launch"
          className="w-full py-3 px-6 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:opacity-60 text-white font-bold text-sm shadow-lg shadow-indigo-600/30 flex items-center justify-center gap-2 cursor-pointer transition-all active:scale-[0.99]"
        >
          {isLoading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('config.launching')}
            </>
          ) : (
            <>
              <Play className="w-4 h-4 fill-white" />
              {syncing ? t('config.launchSync') : t('config.launch')}
            </>
          )}
        </button>
      </div>
    </div>
  );
};

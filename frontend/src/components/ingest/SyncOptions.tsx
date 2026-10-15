import React from 'react';
import { AlertTriangle, Key, Link2, Sparkles } from 'lucide-react';
import { useJobStore } from '../../store/useJobStore';
import { useUiStore } from '../../store/useUiStore';
import { useSettingsStore } from '../../store/useSettingsStore';
import { useT } from '../../i18n';
import { thinkingLabel, modelLabel } from '../settings/labels';
import { sameLanguage } from './languages';

/** "Sync my subtitles": how the lines are matched to the video (replaces the translation card). */
export const SyncOptions: React.FC = () => {
  const pipelineConfig = useJobStore((s) => s.pipelineConfig);
  const updatePipelineConfig = useJobStore((s) => s.updatePipelineConfig);
  const probeResult = useJobStore((s) => s.probeResult);
  const systemInfo = useJobStore((s) => s.systemInfo);
  const syncFile = useJobStore((s) => s.syncFile);
  const syncLanguage = useJobStore((s) => s.syncLanguage);
  const syncUseGemini = useJobStore((s) => s.syncUseGemini);
  const setSyncUseGemini = useJobStore((s) => s.setSyncUseGemini);
  const appSettings = useSettingsStore((s) => s.settings);
  const catalog = useSettingsStore((s) => s.catalog);
  const setView = useUiStore((s) => s.setView);
  const t = useT();

  const keyReady = !!systemInfo?.is_gemini_configured || !!pipelineConfig.gemini_api_key_override;
  const same = sameLanguage(syncLanguage || syncFile?.language, pipelineConfig.source_language);
  // Mirrors the backend: a full (not forced) text track in the video is used as a timing reference
  const embedded = (probeResult?.subtitle_streams || []).find(
    (s) => s.text_based && !s.forced && !/forced|signs/i.test(s.title || '')
  );
  const translationInfo = catalog.find((m) => m.id === appSettings?.translation.model);

  return (
    <div className="space-y-4 p-4 rounded-xl bg-sunken border border-line" data-testid="sync-options">
      <div className="flex items-center gap-2 text-xs font-semibold text-zinc-200 border-b border-line pb-2">
        <Link2 className="w-4 h-4 text-indigo-400" />
        {t('config.syncTitle')}
      </div>
      <p className="text-[11px] text-zinc-400 leading-relaxed">{t('config.syncAuto')}</p>
      {embedded && (
        <p className="text-[11px] text-emerald-400">
          {t('config.syncEmbedded', { name: embedded.title || embedded.language || `#${embedded.index}` })}
        </p>
      )}

      <div className="p-2.5 rounded-lg bg-surface border border-line space-y-2 text-xs">
        <label className="flex items-start justify-between gap-3 cursor-pointer">
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-zinc-200 font-medium">
              <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
              {t('config.syncGeminiTitle')}
            </span>
            <span className="block text-[11px] text-zinc-500 mt-0.5 leading-relaxed">
              {same ? t('config.syncGeminiSameLanguage') : t('config.syncGeminiDescription')}
            </span>
          </span>
          <input
            type="checkbox"
            checked={syncUseGemini}
            onChange={(e) => setSyncUseGemini(e.target.checked)}
            data-testid="sync-use-gemini"
            className="mt-0.5 w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800 cursor-pointer"
          />
        </label>

        {syncUseGemini && !same && (
          <>
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-line">
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
            {!keyReady && (
              <div className="space-y-1.5">
                <div className="flex items-start gap-1.5 text-[11px] text-amber-300">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>{t('config.syncNeedsKey')}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Key className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0" />
                  <input
                    type="password"
                    dir="ltr"
                    placeholder={t('config.keyPlaceholder')}
                    value={pipelineConfig.gemini_api_key_override || ''}
                    onChange={(e) => updatePipelineConfig({ gemini_api_key_override: e.target.value })}
                    className="w-full bg-sunken border border-line rounded px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 font-mono"
                  />
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

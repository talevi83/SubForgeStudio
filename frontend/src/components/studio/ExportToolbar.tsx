import React, { useState } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { api } from '../../services/api';
import { isActiveStatus, isRtlLanguage } from '../../types';
import type { OnscreenPosition } from '../../types';
import { TARGET_LANGUAGES } from '../ingest/languages';
import { useT } from '../../i18n';
import { GlossaryDialog } from './GlossaryDialog';
import { AlertTriangle, Download, FileText, Languages, LayoutTemplate, RotateCw, ScrollText, Users } from 'lucide-react';

export const ExportToolbar: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const applyRtlExport = useJobStore((s) => s.applyRtlExport);
  const setApplyRtlExport = useJobStore((s) => s.setApplyRtlExport);
  const retranslate = useJobStore((s) => s.retranslateActiveJob);
  const updateJobOnscreenPosition = useJobStore((s) => s.updateJobOnscreenPosition);
  const systemInfo = useJobStore((s) => s.systemInfo);
  const keyOverride = useJobStore((s) => s.pipelineConfig.gemini_api_key_override);
  const saveGlossary = useJobStore((s) => s.saveGlossary);
  const [targetLanguage, setTargetLanguage] = useState<string | null>(null);
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const t = useT();

  if (!activeJob) return null;

  const busy = isActiveStatus(activeJob.status);
  const hasSegments = activeJob.segments.some((s) => s.id);
  const hasTranslation = activeJob.segments.some((s) => s.translated_text);
  const failedCount = activeJob.segments.filter((s) => s.translation_failed).length;
  // Lines a translated export would still show in the original language
  const untranslatedCount = hasTranslation ? activeJob.segments.filter((s) => !s.translated_text).length : 0;
  const target = targetLanguage ?? activeJob.target_language;
  const isRtl = isRtlLanguage(target);
  const canTranslate = !!systemInfo?.is_gemini_configured || !!keyOverride;
  const onscreenPos: OnscreenPosition = activeJob.onscreen_position || 'top_center';

  const downloadFile = (url: string) => {
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const translateAll = () => {
    if (hasTranslation && !confirm(t('toolbar.confirmRetranslate', { count: activeJob.segments.length }))) {
      return;
    }
    retranslate({ only_failed: false, target_language: target });
  };

  return (
    <div className="bg-surface border-t border-line p-3 px-4 flex flex-wrap items-center justify-between gap-3">
      {/* Translation controls */}
      <div className="flex items-center gap-2 flex-wrap">
        <Languages className="w-4 h-4 text-indigo-400" />
        <select
          value={target}
          onChange={(e) => setTargetLanguage(e.target.value)}
          disabled={busy}
          className="bg-sunken border border-line rounded-lg px-2 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-indigo-500"
          title={t('toolbar.targetLanguage')}
        >
          {!TARGET_LANGUAGES.some((l) => l.value === activeJob.target_language) && (
            <option value={activeJob.target_language}>{activeJob.target_language}</option>
          )}
          {TARGET_LANGUAGES.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>
        <button
          onClick={translateAll}
          disabled={busy || !hasSegments || !canTranslate}
          title={canTranslate ? t('toolbar.translateTitle') : t('toolbar.needsKey')}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <Languages className="w-3.5 h-3.5" />
          {hasTranslation ? t('toolbar.retranslateAll') : t('toolbar.translate')}
        </button>
        <button
          onClick={() => setGlossaryOpen(true)}
          disabled={busy || !hasSegments}
          title={t('glossary.openTitle')}
          data-testid="open-glossary"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <Users className="w-3.5 h-3.5 text-zinc-400" />
          {t('glossary.open')}
        </button>
        {failedCount > 0 && (
          <button
            onClick={() => retranslate({ only_failed: true })}
            disabled={busy || !canTranslate}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-rose-500/15 hover:bg-rose-500/25 disabled:opacity-40 text-rose-300 border border-rose-500/30 rounded-lg text-xs font-medium transition-colors cursor-pointer"
          >
            <RotateCw className="w-3.5 h-3.5" />
            {t('toolbar.retranslateFailed', { count: failedCount })}
          </button>
        )}
        <div
          className="flex items-center gap-1.5 bg-sunken border border-line rounded-lg px-2 py-1 text-xs text-zinc-300"
          title={t('toolbar.onscreenPositionTitle')}
        >
          <LayoutTemplate className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" />
          <span className="text-[11px] text-zinc-400 whitespace-nowrap">{t('toolbar.onscreenPositionLabel')}</span>
          <select
            aria-label={t('toolbar.onscreenPositionLabel')}
            data-testid="studio-onscreen-position"
            value={onscreenPos}
            onChange={(e) => void updateJobOnscreenPosition(e.target.value as OnscreenPosition)}
            className="bg-transparent border-0 text-xs text-cyan-300 font-medium focus:outline-none cursor-pointer"
          >
            <option value="top_center" className="bg-zinc-900 text-zinc-200">{t('settings.pos.top_center')}</option>
            <option value="middle_right" className="bg-zinc-900 text-zinc-200">{t('settings.pos.middle_right')}</option>
            <option value="middle_left" className="bg-zinc-900 text-zinc-200">{t('settings.pos.middle_left')}</option>
            <option value="top_right" className="bg-zinc-900 text-zinc-200">{t('settings.pos.top_right')}</option>
            <option value="top_left" className="bg-zinc-900 text-zinc-200">{t('settings.pos.top_left')}</option>
          </select>
        </div>
      </div>

      {glossaryOpen && (
        <GlossaryDialog
          glossary={activeJob.glossary}
          profile={activeJob.glossary_profile}
          canTranslate={canTranslate}
          onClose={() => setGlossaryOpen(false)}
          onSave={async (g, thenTranslate, profile) => {
            const ok = await saveGlossary(g, profile);
            if (!ok) return;
            setGlossaryOpen(false);
            if (thenTranslate) retranslate({ only_failed: false, target_language: target });
          }}
        />
      )}

      {/* Export */}
      <div className="flex items-center gap-2 flex-wrap">
        {untranslatedCount > 0 && (
          <span
            className="flex items-center gap-1 text-[11px] text-amber-300 me-1"
            data-testid="untranslated-note"
            title={t('toolbar.untranslatedTitle')}
          >
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
            {busy
              ? t('toolbar.stillTranslating', { count: untranslatedCount })
              : t('toolbar.untranslated', { count: untranslatedCount })}
          </span>
        )}
        <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer select-none me-2" title={t('toolbar.rtlTitle')}>
          <input
            type="checkbox"
            checked={applyRtlExport}
            onChange={(e) => setApplyRtlExport(e.target.checked)}
            className="w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800"
          />
          <span className="font-medium">{t('toolbar.rtlFix')}</span>
          {isRtl && (
            <span className="text-[10px] bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 px-1.5 rounded font-semibold">
              {t('toolbar.recommended')}
            </span>
          )}
        </label>

        {hasTranslation && (
          <button
            onClick={() => downloadFile(api.getExportUrl(activeJob.id, 'srt', true, applyRtlExport))}
            disabled={!hasSegments}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:opacity-40 text-white rounded-lg text-xs font-semibold shadow-md shadow-indigo-600/20 transition-all cursor-pointer"
          >
            <Download className="w-3.5 h-3.5" />
            {t('toolbar.translatedSrt')}
          </button>
        )}
        <button
          onClick={() => downloadFile(api.getExportUrl(activeJob.id, 'srt', false, false))}
          disabled={!hasSegments}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <FileText className="w-3.5 h-3.5 text-zinc-400" />
          {t('toolbar.originalSrt')}
        </button>
        <button
          onClick={() => downloadFile(api.getExportUrl(activeJob.id, 'vtt', hasTranslation, hasTranslation && applyRtlExport))}
          disabled={!hasSegments}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <FileText className="w-3.5 h-3.5 text-zinc-400" />
          {t('toolbar.webvtt')}
        </button>
        <button
          onClick={() => downloadFile(api.getJobLogUrl(activeJob.id))}
          title={t('toolbar.jobLogTitle')}
          data-testid="job-log"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <ScrollText className="w-3.5 h-3.5 text-zinc-400" />
          {t('toolbar.jobLog')}
        </button>
      </div>
    </div>
  );
};

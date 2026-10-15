import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { isActiveStatus } from '../../types';
import { languageLabel } from '../ingest/languages';
import { useT, formatUsd } from '../../i18n';
import type { MessageKey } from '../../i18n';
import { useUiStore } from '../../store/useUiStore';
import {
  Search,
  Music,
  Mic,
  Sliders,
  Languages,
  Clock,
  Zap,
  StopCircle,
  AlertTriangle,
  XCircle,
  Hourglass,
  ScanText,
  Coins,
  TimerReset,
  CheckCircle2,
} from 'lucide-react';
import type { SyncReport } from '../../types';

const ALL_STAGES = [
  { key: 'PROBING', icon: Search },
  { key: 'EXTRACTING_AUDIO', icon: Music },
  { key: 'TRANSCRIBING', icon: Mic },
  { key: 'SCANNING_ONSCREEN', icon: ScanText },
  { key: 'REFINING', icon: Sliders },
  { key: 'TRANSLATING', icon: Languages },
  { key: 'SYNCING', icon: TimerReset },
];

const formatEta = (seconds?: number | null, language: string = 'en') => {
  if (!seconds || seconds <= 0) return null;
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (language === 'he') return mins > 0 ? `${mins} דק׳ ${secs} שנ׳` : `${secs} שנ׳`;
  return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
};

export const LivePipelineBar: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const cancelActiveJob = useJobStore((s) => s.cancelActiveJob);
  const systemInfo = useJobStore((s) => s.systemInfo);
  const stageMessage = useJobStore((s) => s.stageMessage);
  const language = useUiStore((s) => s.language);
  const t = useT();

  if (!activeJob) return null;

  const status = activeJob.status;
  const isCompleted = status === 'COMPLETED';
  const isFailed = status === 'FAILED';
  const isCancelled = status === 'CANCELLED';
  const isQueued = status === 'QUEUED';
  const isRunning = isActiveStatus(status);

  // A translation-only run (Retranslate) shows just the translate step
  const translateOnly = status === 'TRANSLATING' && activeJob.segments.some((s) => s.id);
  const isSync = activeJob.job_kind === 'sync';
  const stages = ALL_STAGES.filter(
    (s) =>
      (isSync
        ? s.key !== 'REFINING' && s.key !== 'TRANSLATING'
        : s.key !== 'SYNCING' && (s.key !== 'TRANSLATING' || activeJob.enable_translation || status === 'TRANSLATING')) &&
      (s.key !== 'SCANNING_ONSCREEN' || activeJob.detect_onscreen_text)
  );
  const order = stages.map((s) => s.key);

  const stageState = (key: string) => {
    if (isCompleted) return 'done';
    if (isFailed || isCancelled) return 'idle';
    if (isQueued) return order.indexOf(key) < order.indexOf('TRANSCRIBING') ? 'done' : 'idle';
    if (translateOnly) return key === 'TRANSLATING' ? 'active' : 'done';
    const current = order.indexOf(status);
    const idx = order.indexOf(key);
    if (idx < current) return 'done';
    if (idx === current) return 'active';
    return 'idle';
  };

  const isCuda = systemInfo?.hardware?.cuda_available;
  const usageTotal = activeJob.usage?.total_cost_usd;
  const usageLines = Object.entries(activeJob.usage?.stages || {})
    .map(([stage, u]) => `${t(`stages.${stage}` as MessageKey)}: ${u!.model} · ${u!.requests}× · ${formatUsd(u!.cost_usd, language)}`)
    .join('\n');

  return (
    <div className="bg-surface border-b border-line px-6 py-3 shadow-md space-y-2">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-1 flex-1 overflow-x-auto py-1">
          {isQueued && (
            <div className="flex items-center gap-2 px-3 py-1.5 me-2 rounded-lg text-xs font-medium bg-amber-500/10 text-amber-300 border border-amber-500/30">
              <Hourglass className="w-3.5 h-3.5 animate-pulse" />
              {t('monitor.queued')}
            </div>
          )}
          {stages.map((s, idx) => {
            const state = stageState(s.key);
            const Icon = s.icon;
            return (
              <React.Fragment key={s.key}>
                <div
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    state === 'active'
                      ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/40 shadow-sm'
                      : state === 'done'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'bg-sunken text-zinc-500 border border-line'
                  }`}
                >
                  <Icon
                    className={`w-3.5 h-3.5 ${
                      state === 'active' ? 'animate-pulse text-indigo-400' : state === 'done' ? 'text-emerald-400' : 'text-zinc-600'
                    }`}
                  />
                  <span className="whitespace-nowrap">{t(`stages.${s.key}` as MessageKey)}</span>
                </div>
                {idx < stages.length - 1 && <div className="w-3 h-0.5 bg-line flex-shrink-0" />}
              </React.Fragment>
            );
          })}
        </div>

        <div className="flex items-center gap-3 text-xs flex-shrink-0">
          {activeJob.comms_boost && (
            <div className="px-2.5 py-1 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-300" title={t('config.commsTitle')}>
              📻 {t('monitor.comms')}
            </div>
          )}
          {activeJob.detected_language && (
            <div className="px-2.5 py-1 rounded bg-sunken border border-line text-zinc-300" title={t('monitor.detectedLanguage')}>
              {languageLabel(activeJob.detected_language)}
            </div>
          )}
          {isRunning && activeJob.speed_factor ? (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-sunken border border-line text-zinc-300 font-mono">
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              <span>{activeJob.speed_factor}x</span>
            </div>
          ) : null}
          {isRunning && formatEta(activeJob.eta_seconds, language) && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-sunken border border-line text-zinc-300 font-mono">
              <Clock className="w-3.5 h-3.5 text-indigo-400" />
              <span dir={language === 'he' ? 'rtl' : 'ltr'}>
                {t('monitor.eta', { time: formatEta(activeJob.eta_seconds, language) || '' })}
              </span>
            </div>
          )}
          {usageTotal ? (
            <div
              className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-sunken border border-line text-zinc-300"
              title={`${t('monitor.geminiCostTitle')}\n${usageLines}`}
              data-testid="gemini-cost"
            >
              <Coins className="w-3.5 h-3.5 text-amber-400" />
              <span dir="ltr">{t('monitor.geminiCost', { cost: formatUsd(usageTotal, language) })}</span>
            </div>
          ) : null}
          <div className="px-2.5 py-1 rounded bg-zinc-800 border border-zinc-700/60 text-zinc-300 font-semibold text-[11px]">
            {isCuda ? 'CUDA FP16' : 'CPU INT8'}
          </div>
          {isRunning && (
            <button
              onClick={cancelActiveJob}
              className="flex items-center gap-1 px-3 py-1 rounded-md bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/30 font-medium transition-colors cursor-pointer"
            >
              <StopCircle className="w-3.5 h-3.5" />
              {t('monitor.cancel')}
            </button>
          )}
        </div>
      </div>

      <div className="w-full bg-sunken h-1.5 rounded-full overflow-hidden border border-line">
        <div
          className={`h-full transition-all duration-300 rounded-full ${
            isFailed ? 'bg-rose-500' : isCompleted ? 'bg-emerald-500' : 'bg-gradient-to-r from-indigo-500 to-violet-500'
          }`}
          style={{ width: `${Math.min(100, Math.max(0, activeJob.progress_percent))}%` }}
        />
      </div>

      {isRunning && stageMessage && <div className="text-[11px] text-zinc-400 truncate">{stageMessage}</div>}

      {isFailed && activeJob.error_message && (
        <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs flex items-start gap-2">
          <XCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span className="break-all">{activeJob.error_message}</span>
        </div>
      )}
      {isSync && isCompleted && activeJob.sync_report && <SyncSummary report={activeJob.sync_report} />}

      {!isRunning && activeJob.warning_message && (
        <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>{activeJob.warning_message}</span>
        </div>
      )}
    </div>
  );
};

/** How a finished sync placed the lines (methods, frame rate, delay, cuts). */
const SyncSummary: React.FC<{ report: SyncReport }> = ({ report }) => {
  const t = useT();
  const methods = report.methods || {};
  const order = ['text', 'gemini', 'screen', 'burned_in', 'embedded', 'speech'] as const;
  const parts = order
    .filter((m) => (methods[m] || 0) > 0)
    .map((m) => t(`monitor.syncMethods.${m}` as MessageKey, { count: methods[m] || 0 }));
  const extras: string[] = [];
  if (report.frame_rate && report.frame_rate !== 'same frame rate') extras.push(t('monitor.syncFrameRate', { value: report.frame_rate.replace('->', '→') }));
  if (typeof report.offset === 'number' && Math.abs(report.offset) >= 0.05)
    extras.push(t('monitor.syncOffset', { value: `${report.offset > 0 ? '+' : ''}${report.offset.toFixed(1)}` }));
  if (report.jump_count) extras.push(t('monitor.syncCuts', { count: report.jump_count }));
  const uncertain = methods.low_confidence || 0;
  return (
    <div
      className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs flex items-start gap-2"
      data-testid="sync-summary"
    >
      <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <span>
        <span className="font-semibold">{t('monitor.syncResult', { lines: report.lines || 0 })}</span>
        {parts.length > 0 && <>: {parts.join(' · ')}</>}
        {extras.length > 0 && <span className="text-emerald-300/70" dir="auto"> ({extras.join(', ')})</span>}
        {uncertain > 0 && <span className="text-amber-300"> — {t('monitor.syncUncertain', { count: uncertain })}</span>}
      </span>
    </div>
  );
};

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
  Cpu,
} from 'lucide-react';
import type { JobUsage, ModelUsage, SyncReport } from '../../types';

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

      {activeJob.usage && <UsageSummaryBar usage={activeJob.usage} isRunning={isRunning} language={language} />}

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

const aggregateModels = (usage: JobUsage): ModelUsage[] => {
  if (usage.models && Object.keys(usage.models).length > 0) {
    return Object.values(usage.models);
  }
  const map = new Map<string, ModelUsage>();
  for (const st of Object.values(usage.stages || {})) {
    if (!st) continue;
    const key = st.model || 'unknown';
    const existing = map.get(key);
    const total = (st.input_tokens || 0) + (st.output_tokens || 0) + (st.thinking_tokens || 0);
    if (!existing) {
      map.set(key, {
        ...st,
        model: key,
        total_tokens: total,
      });
    } else {
      existing.requests += st.requests || 0;
      existing.input_tokens += st.input_tokens || 0;
      existing.output_tokens += st.output_tokens || 0;
      existing.thinking_tokens += st.thinking_tokens || 0;
      existing.total_tokens += total;
      existing.cost_usd =
        existing.cost_usd === null || st.cost_usd === null ? null : (existing.cost_usd || 0) + (st.cost_usd || 0);
    }
  }
  return Array.from(map.values());
};

/** Live and post-run summary of tokens per model, local AI queries, and saved tokens. */
const UsageSummaryBar: React.FC<{ usage: JobUsage; isRunning: boolean; language: 'en' | 'he' }> = ({
  usage,
  isRunning,
  language,
}) => {
  const t = useT();
  const models = aggregateModels(usage);
  const local = usage.local;
  const hasLocalActivity = !!(local && (local.requests > 0 || local.saved_tokens > 0 || local.frames_filtered > 0));
  if (models.length === 0 && !hasLocalActivity) return null;

  const localRequests = local?.requests || 0;
  const savedTokens = local?.saved_tokens || 0;
  const framesFiltered = local?.frames_filtered || 0;

  return (
    <div
      className="p-2.5 rounded-lg bg-sunken/80 border border-line text-xs flex flex-wrap items-center justify-between gap-2"
      data-testid="usage-summary-bar"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-zinc-200 flex items-center gap-1.5 me-1">
          <Coins className={`w-3.5 h-3.5 ${isRunning ? 'text-indigo-400 animate-pulse' : 'text-amber-400'}`} />
          {isRunning ? t('monitor.usageSummaryLive') : t('monitor.usageSummaryDone')}:
        </span>

        {models.map((m) => {
          const total = m.total_tokens ?? m.input_tokens + m.output_tokens + m.thinking_tokens;
          return (
            <div
              key={m.model}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface border border-line text-zinc-200"
              data-testid={`usage-model-${m.model}`}
            >
              <span className="font-mono font-semibold text-indigo-300" dir="ltr">
                {m.model}
              </span>
              <span className="text-zinc-500">·</span>
              <span className="font-medium">
                {t('monitor.usageModelTokens', { tokens: total.toLocaleString() })}
              </span>
              <span className="text-zinc-400 text-[11px]">
                ({t('monitor.usageModelBreakdown', {
                  input: m.input_tokens.toLocaleString(),
                  output: m.output_tokens.toLocaleString(),
                  thinking: m.thinking_tokens.toLocaleString(),
                })})
              </span>
              <span className="text-zinc-500">·</span>
              <span className="text-zinc-300">{t('monitor.usageRequests', { count: m.requests })}</span>
              {m.cost_usd !== null && (
                <>
                  <span className="text-zinc-500">·</span>
                  <span className="font-mono text-amber-300" dir="ltr">
                    {formatUsd(m.cost_usd, language)}
                  </span>
                </>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface border border-line text-zinc-300"
          data-testid="usage-local-queries"
        >
          <Cpu className="w-3.5 h-3.5 text-sky-400" />
          <span>
            {local?.model
              ? t('monitor.localAiQueries', { model: local.model, count: localRequests })
              : t('monitor.localAiNoQueries', { count: localRequests })}
          </span>
        </div>

        <div
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border font-medium ${
            savedTokens > 0
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-surface border-line text-zinc-400'
          }`}
          data-testid="usage-saved-tokens"
        >
          <span>{t('monitor.localSavedTokens', { tokens: savedTokens.toLocaleString() })}</span>
          {framesFiltered > 0 && (
            <span className="text-emerald-300/80 text-[11px]">
              {t('monitor.localSavedFrames', { frames: framesFiltered })}
            </span>
          )}
          {local && local.saved_cost_usd > 0 && (
            <span className="font-mono text-emerald-300/90" dir="ltr">
              (~{formatUsd(local.saved_cost_usd, language)})
            </span>
          )}
        </div>
      </div>
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

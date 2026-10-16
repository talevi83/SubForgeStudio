import React, { useEffect } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { useUiStore } from '../../store/useUiStore';
import { useT, formatUsd } from '../../i18n';
import { isActiveStatus } from '../../types';
import { Film, PlusCircle, Cpu, Trash2, CheckCircle2, AlertCircle, Loader2, Sparkles, Settings, HelpCircle } from 'lucide-react';

export const Sidebar: React.FC = () => {
  const jobs = useJobStore((s) => s.jobs);
  const activeJob = useJobStore((s) => s.activeJob);
  const systemInfo = useJobStore((s) => s.systemInfo);
  const fetchJobs = useJobStore((s) => s.fetchJobs);
  const fetchSystemInfo = useJobStore((s) => s.fetchSystemInfo);
  const selectJob = useJobStore((s) => s.selectJob);
  const clearActiveJob = useJobStore((s) => s.clearActiveJob);
  const deleteJob = useJobStore((s) => s.deleteJob);
  const view = useUiStore((s) => s.view);
  const setView = useUiStore((s) => s.setView);
  const language = useUiStore((s) => s.language);
  const t = useT();

  useEffect(() => {
    fetchJobs();
    fetchSystemInfo();
  }, [fetchJobs, fetchSystemInfo]);

  // Jobs running in the background (not the one open in the studio) get no WebSocket
  // updates here, so poll the list while any of them is still working.
  const hasBackgroundWork = jobs.some((j) => isActiveStatus(j.status) && j.id !== activeJob?.id);
  useEffect(() => {
    if (!hasBackgroundWork) return;
    const timer = setInterval(fetchJobs, 3000);
    return () => clearInterval(timer);
  }, [hasBackgroundWork, fetchJobs]);

  const hw = systemInfo?.hardware;
  const isCuda = hw?.cuda_available;
  const isGeminiReady = systemInfo?.is_gemini_configured;

  return (
    <aside className="w-80 h-screen bg-sunken border-e border-line flex flex-col flex-shrink-0 select-none">
      <div className="p-4 border-b border-line flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
          <Film className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="font-bold text-base tracking-tight text-zinc-50 flex items-center gap-1.5" dir="ltr">
            SubForge
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 font-semibold tracking-wider uppercase border border-indigo-500/30">
              Studio
            </span>
          </h1>
          <p className="text-[11px] text-zinc-400 font-medium">{t('sidebar.tagline')}</p>
        </div>
      </div>

      <div className="p-3">
        <button
          onClick={() => {
            clearActiveJob();
            setView('studio');
          }}
          className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md shadow-indigo-600/25 transition-all active:scale-[0.98] cursor-pointer"
        >
          <PlusCircle className="w-4 h-4" />
          <span>{t('sidebar.newProject')}</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
        <div className="flex items-center justify-between px-2 pb-1 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">
          <span>{t('sidebar.recent')}</span>
          <span className="text-[10px] bg-zinc-800 px-1.5 rounded text-zinc-400">{jobs.length}</span>
        </div>

        {jobs.length === 0 ? (
          <div className="px-4 py-8 text-center text-zinc-400 text-xs">{t('sidebar.empty')}</div>
        ) : (
          jobs.map((job) => {
            const isSelected = activeJob?.id === job.id && view === 'studio';
            const isProcessing = isActiveStatus(job.status);
            const cost = job.usage?.total_cost_usd;

            return (
              <div
                key={job.id}
                onClick={() => {
                  selectJob(job.id);
                  setView('studio');
                }}
                className={`group relative flex flex-col p-2.5 rounded-lg border text-start transition-all cursor-pointer ${
                  isSelected
                    ? 'bg-zinc-800/80 border-indigo-500/50 shadow-sm'
                    : 'bg-surface/60 hover:bg-zinc-800/40 border-transparent hover:border-zinc-700/60'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium text-zinc-200 truncate flex-1" dir="auto">
                    {job.file_name}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      const extra = job.is_upload ? `\n\n${t('sidebar.confirmDeleteUpload')}` : '';
                      if (confirm(`${t('sidebar.confirmDelete', { name: job.file_name })}${extra}`)) {
                        deleteJob(job.id);
                      }
                    }}
                    className="opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-rose-400 p-0.5 rounded transition-opacity"
                    title={t('sidebar.deleteTitle')}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>

                <div className="mt-2 flex items-center justify-between text-[11px] gap-2">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {job.status === 'COMPLETED' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 font-medium">
                        <CheckCircle2 className="w-3 h-3" />
                        {t('status.COMPLETED')}
                      </span>
                    )}
                    {job.status === 'COMPLETED' && (job.failed_translation_count > 0 || job.warning_message) && (
                      <span
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 font-medium"
                        title={job.warning_message || undefined}
                      >
                        <AlertCircle className="w-3 h-3" />
                        {job.failed_translation_count > 0
                          ? t('sidebar.untranslated', { count: job.failed_translation_count })
                          : t('sidebar.warning')}
                      </span>
                    )}
                    {job.status === 'FAILED' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-rose-500/10 text-rose-400 font-medium">
                        <AlertCircle className="w-3 h-3" />
                        {t('status.FAILED')}
                      </span>
                    )}
                    {job.status === 'CANCELLED' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-zinc-500/10 text-zinc-400 font-medium">
                        {t('status.CANCELLED')}
                      </span>
                    )}
                    {isProcessing && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-indigo-500/10 text-indigo-400 font-medium animate-pulse">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        {job.status === 'QUEUED'
                          ? t('status.QUEUED')
                          : `${t(`status.${job.status}`)} (${Math.round(job.progress_percent)}%)`}
                      </span>
                    )}
                  </div>
                  <span className="text-zinc-400 text-[10px] whitespace-nowrap">
                    {job.job_kind === 'sync' && (
                      <span className="me-1.5 px-1 rounded bg-indigo-500/15 text-indigo-300" data-testid="sync-badge">
                        {t('sidebar.syncBadge')}
                      </span>
                    )}
                    {t('sidebar.subs', { count: job.segment_count || 0 })}
                    {cost ? ` · ${formatUsd(cost, language)}` : ''}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className="p-3 border-t border-line bg-sunken space-y-2 text-xs">
        <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-raised border border-line">
          <div className="flex items-center gap-2 min-w-0">
            <Cpu className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0" />
            <span className="text-[11px] font-medium text-zinc-300 truncate">
              {isCuda ? hw?.gpu_name || t('sidebar.cudaFallback') : t('sidebar.cpuMode')}
            </span>
          </div>
          <span
            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
              isCuda ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' : 'bg-zinc-700/40 text-zinc-400'
            }`}
          >
            {isCuda ? 'CUDA FP16' : 'CPU'}
          </span>
        </div>

        <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-raised border border-line">
          <div className="flex items-center gap-2">
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span className="text-[11px] font-medium text-zinc-300">{t('sidebar.gemini')}</span>
          </div>
          <span
            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
              isGeminiReady
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
            }`}
          >
            {isGeminiReady ? t('sidebar.geminiActive') : t('sidebar.geminiNoKey')}
          </span>
        </div>

        <button
          onClick={() => setView(view === 'help' ? 'studio' : 'help')}
          data-testid="sidebar-help-btn"
          className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-md border text-[11px] font-medium transition-colors cursor-pointer ${
            view === 'help'
              ? 'bg-indigo-600/15 border-indigo-500/50 text-indigo-300'
              : 'bg-raised border-line text-zinc-300 hover:border-zinc-600'
          }`}
        >
          <HelpCircle className="w-3.5 h-3.5" />
          {t('sidebar.help')}
        </button>

        <button
          onClick={() => setView(view === 'settings' ? 'studio' : 'settings')}
          className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-md border text-[11px] font-medium transition-colors cursor-pointer ${
            view === 'settings'
              ? 'bg-indigo-600/15 border-indigo-500/50 text-indigo-300'
              : 'bg-raised border-line text-zinc-300 hover:border-zinc-600'
          }`}
        >
          <Settings className="w-3.5 h-3.5" />
          {t('sidebar.settings')}
        </button>
      </div>
    </aside>
  );
};

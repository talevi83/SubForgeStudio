import React, { useEffect } from 'react';
import { useJobStore } from '../../store/useJobStore';
import {
  Film,
  PlusCircle,
  Cpu,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Sparkles,
} from 'lucide-react';

export const Sidebar: React.FC = () => {
  const {
    jobs,
    activeJob,
    systemInfo,
    fetchJobs,
    fetchSystemInfo,
    selectJob,
    clearActiveJob,
    deleteJob,
  } = useJobStore();

  useEffect(() => {
    fetchJobs();
    fetchSystemInfo();
  }, [fetchJobs, fetchSystemInfo]);

  const hw = systemInfo?.hardware;
  const isCuda = hw?.cuda_available;
  const isGeminiReady = systemInfo?.is_gemini_configured;

  return (
    <aside className="w-80 h-screen bg-[#121214] border-r border-[#27272a] flex flex-col flex-shrink-0 select-none">
      {/* Brand Header */}
      <div className="p-4 border-b border-[#27272a] flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Film className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="font-bold text-base tracking-tight text-white flex items-center gap-1.5">
              SubForge
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 font-semibold tracking-wider uppercase border border-indigo-500/30">
                Studio
              </span>
            </h1>
            <p className="text-[11px] text-zinc-400 font-medium">Audiovisual Subtitling</p>
          </div>
        </div>
      </div>

      {/* Action CTA */}
      <div className="p-3">
        <button
          onClick={clearActiveJob}
          className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md shadow-indigo-600/25 transition-all active:scale-[0.98] cursor-pointer"
        >
          <PlusCircle className="w-4 h-4" />
          <span>New Subtitling Project</span>
        </button>
      </div>

      {/* Job History List */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
        <div className="flex items-center justify-between px-2 pb-1 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">
          <span>Recent Projects</span>
          <span className="text-[10px] bg-zinc-800 px-1.5 py-0.2 rounded text-zinc-400">
            {jobs.length}
          </span>
        </div>

        {jobs.length === 0 ? (
          <div className="px-4 py-8 text-center text-zinc-400 text-xs">
            No projects yet. Ingest a video to start.
          </div>
        ) : (
          jobs.map((job) => {
            const isSelected = activeJob?.id === job.id;
            const isProcessing =
              job.status !== 'COMPLETED' &&
              job.status !== 'FAILED' &&
              job.status !== 'CANCELLED' &&
              job.status !== 'IDLE';

            return (
              <div
                key={job.id}
                onClick={() => selectJob(job.id)}
                className={`group relative flex flex-col p-2.5 rounded-lg border text-left transition-all cursor-pointer ${
                  isSelected
                    ? 'bg-zinc-800/80 border-indigo-500/50 shadow-sm'
                    : 'bg-[#18181b]/60 hover:bg-zinc-800/40 border-transparent hover:border-zinc-700/60'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium text-zinc-200 truncate flex-1 title-truncate">
                    {job.file_name}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete project "${job.file_name}"?`)) {
                        deleteJob(job.id);
                      }
                    }}
                    className="opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-rose-400 p-0.5 rounded transition-opacity"
                    title="Delete project"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>

                <div className="mt-2 flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-1.5">
                    {job.status === 'COMPLETED' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 font-medium">
                        <CheckCircle2 className="w-3 h-3" />
                        Completed
                      </span>
                    )}
                    {job.status === 'FAILED' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-rose-500/10 text-rose-400 font-medium">
                        <AlertCircle className="w-3 h-3" />
                        Failed
                      </span>
                    )}
                    {job.status === 'CANCELLED' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-zinc-500/10 text-zinc-400 font-medium">
                        Cancelled
                      </span>
                    )}
                    {isProcessing && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-indigo-500/10 text-indigo-400 font-medium animate-pulse">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        {job.status} ({Math.round(job.progress_percent)}%)
                      </span>
                    )}
                  </div>

                  <span className="text-zinc-400 text-[10px]">
                    {job.segments?.length || 0} segs
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* System Status Badges */}
      <div className="p-3 border-t border-[#27272a] bg-[#151518] space-y-2 text-xs">
        {/* Hardware Compute Badge */}
        <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-[#1d1d20] border border-[#27272a]">
          <div className="flex items-center gap-2">
            <Cpu className="w-3.5 h-3.5 text-zinc-400" />
            <span className="text-[11px] font-medium text-zinc-300">
              {isCuda ? hw?.gpu_name || 'CUDA Acceleration' : 'CPU INT8 Mode'}
            </span>
          </div>
          <span
            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
              isCuda
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'bg-zinc-700/40 text-zinc-400'
            }`}
          >
            {isCuda ? 'CUDA FP16' : 'CPU'}
          </span>
        </div>

        {/* Gemini API Status Badge */}
        <div className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-[#1d1d20] border border-[#27272a]">
          <div className="flex items-center gap-2">
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span className="text-[11px] font-medium text-zinc-300">Gemini 2.5 Translation</span>
          </div>
          <span
            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
              isGeminiReady
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
            }`}
          >
            {isGeminiReady ? 'Active' : 'No Key (.env)'}
          </span>
        </div>
      </div>
    </aside>
  );
};

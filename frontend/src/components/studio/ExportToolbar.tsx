import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { api } from '../../services/api';
import { Download, FileText } from 'lucide-react';

export const ExportToolbar: React.FC = () => {
  const { activeJob, applyRtlExport, setApplyRtlExport } = useJobStore();

  if (!activeJob) return null;

  const isRtl =
    activeJob.target_language?.toLowerCase() === 'hebrew' ||
    activeJob.target_language?.toLowerCase() === 'arabic';

  const downloadFile = (url: string) => {
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  return (
    <div className="bg-[#18181b] border-t border-[#27272a] p-3 px-4 flex flex-wrap items-center justify-between gap-4">
      {/* RTL BiDi Option */}
      <div className="flex items-center space-x-2">
        <label className="flex items-center space-x-2 text-xs text-zinc-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={applyRtlExport}
            onChange={(e) => setApplyRtlExport(e.target.checked)}
            className="w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800"
          />
          <span className="font-medium">Apply BiDi RTL Punctuation Fix</span>
        </label>
        {isRtl && (
          <span className="text-[10px] bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 px-1.5 py-0.2 rounded font-semibold">
            Recommended for Hebrew
          </span>
        )}
      </div>

      {/* Export Action Buttons */}
      <div className="flex items-center space-x-2">
        {/* Export Translated SRT */}
        {activeJob.enable_translation && (
          <button
            onClick={() =>
              downloadFile(api.getExportSrtUrl(activeJob.id, true, applyRtlExport))
            }
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-indigo-600/20 transition-all cursor-pointer"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Download Translated .SRT</span>
          </button>
        )}

        {/* Export Original SRT */}
        <button
          onClick={() =>
            downloadFile(api.getExportSrtUrl(activeJob.id, false, false))
          }
          className="flex items-center space-x-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <FileText className="w-3.5 h-3.5 text-zinc-400" />
          <span>Original .SRT</span>
        </button>

        {/* Export WebVTT */}
        <button
          onClick={() =>
            downloadFile(
              api.getExportVttUrl(
                activeJob.id,
                activeJob.enable_translation,
                applyRtlExport
              )
            )
          }
          className="flex items-center space-x-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700/60 rounded-lg text-xs font-medium transition-colors cursor-pointer"
        >
          <FileText className="w-3.5 h-3.5 text-zinc-400" />
          <span>WebVTT (.vtt)</span>
        </button>
      </div>
    </div>
  );
};

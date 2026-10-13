import React, { useState } from 'react';
import { api } from '../../services/api';
import { useJobStore } from '../../store/useJobStore';
import { Upload, HardDrive, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react';

export const MediaDropzone: React.FC = () => {
  const { setProbeResult, probeResult } = useJobStore();
  const [tab, setTab] = useState<'local' | 'upload'>('local');
  const [localPath, setLocalPath] = useState('');
  const [isProbing, setIsProbing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleProbeLocalPath = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!localPath.trim()) return;

    setIsProbing(true);
    setError(null);
    try {
      const res = await api.probePath(localPath.trim());
      setProbeResult(res);
    } catch (err: any) {
      setError(err.message || 'Failed to inspect local video file');
    } finally {
      setIsProbing(false);
    }
  };

  const handleFileUpload = async (file: File) => {
    setIsProbing(true);
    setError(null);
    try {
      const res = await api.uploadFile(file);
      setProbeResult(res);
    } catch (err: any) {
      setError(err.message || 'File upload failed');
    } finally {
      setIsProbing(false);
    }
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  };

  return (
    <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-5 shadow-lg">
      <div className="flex items-center justify-between border-b border-[#27272a] pb-3 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-indigo-400" />
            Media File Ingestion
          </h2>
          <p className="text-xs text-zinc-400 mt-0.5">
            Select an existing movie/episode on disk or upload via browser
          </p>
        </div>

        {/* Tab switch */}
        <div className="flex bg-[#121214] p-1 rounded-lg border border-[#27272a]">
          <button
            onClick={() => setTab('local')}
            className={`px-3 py-1 rounded text-xs font-medium transition-all ${
              tab === 'local'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Direct Local Path
          </button>
          <button
            onClick={() => setTab('upload')}
            className={`px-3 py-1 rounded text-xs font-medium transition-all ${
              tab === 'upload'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            File Upload
          </button>
        </div>
      </div>

      {tab === 'local' ? (
        <form onSubmit={handleProbeLocalPath} className="space-y-3">
          <label className="block text-xs font-medium text-zinc-300">
            Absolute Local File Path (Zero-Copy Processing)
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
              placeholder="e.g. C:\Movies\Inception.2010.1080p.mkv or I:\Media\episode.mp4"
              className="flex-1 bg-[#121214] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 font-mono"
            />
            <button
              type="submit"
              disabled={isProbing || !localPath.trim()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-xs font-semibold rounded-lg transition-colors flex items-center gap-2 cursor-pointer"
            >
              {isProbing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Inspecting...
                </>
              ) : (
                'Inspect Media'
              )}
            </button>
          </div>
          <p className="text-[11px] text-zinc-500">
            Files on local drives (1GB – 50GB+) are probed in-place instantly without duplicating bytes.
          </p>
        </form>
      ) : (
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
          className="border-2 border-dashed border-[#27272a] hover:border-indigo-500/50 rounded-xl p-8 text-center bg-[#121214]/50 transition-colors flex flex-col items-center justify-center cursor-pointer"
          onClick={() => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = 'video/*,audio/*,.mkv,.mp4,.avi,.mov';
            input.onchange = (e: any) => {
              if (e.target.files?.[0]) handleFileUpload(e.target.files[0]);
            };
            input.click();
          }}
        >
          <Upload className="w-8 h-8 text-indigo-400 mb-2" />
          <p className="text-xs font-medium text-zinc-300">
            Drag & drop a video file here, or click to browse
          </p>
          <p className="text-[11px] text-zinc-500 mt-1">Supports MKV, MP4, WebM, AVI, MOV</p>
          {isProbing && (
            <div className="mt-3 flex items-center gap-2 text-xs text-indigo-400 font-medium">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Uploading and inspecting media...
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="mt-3 p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {probeResult && (
        <div className="mt-3 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          <span>
            Successfully probed: <strong>{probeResult.file_name}</strong> (
            {Math.round(probeResult.file_size / (1024 * 1024))} MB, {Math.round(probeResult.duration)}s)
          </span>
        </div>
      )}
    </div>
  );
};

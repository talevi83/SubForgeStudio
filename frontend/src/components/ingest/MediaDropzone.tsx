import React, { useState } from 'react';
import { api } from '../../services/api';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { formatClock } from '../../utils/time';
import { Upload, HardDrive, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react';

export const MediaDropzone: React.FC = () => {
  const setProbeResult = useJobStore((s) => s.setProbeResult);
  const probeResult = useJobStore((s) => s.probeResult);
  const t = useT();
  const [tab, setTab] = useState<'local' | 'upload'>('local');
  const [localPath, setLocalPath] = useState('');
  const [isProbing, setIsProbing] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleProbeLocalPath = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!localPath.trim()) return;
    setIsProbing(true);
    setError(null);
    try {
      setProbeResult(await api.probePath(localPath.trim()));
    } catch (err: any) {
      setError(err.message || t('dropzone.probeFailed'));
    } finally {
      setIsProbing(false);
    }
  };

  const handleFileUpload = async (file: File) => {
    setIsProbing(true);
    setError(null);
    try {
      // Same file uploaded before? Reuse it instead of copying gigabytes again.
      const existing = await api.findExistingUpload(file);
      if (existing) {
        setProbeResult(existing);
        return;
      }
      setUploadProgress(0);
      setProbeResult(await api.uploadFile(file, setUploadProgress));
    } catch (err: any) {
      setError(err.message || t('dropzone.uploadFailed'));
    } finally {
      setIsProbing(false);
      setUploadProgress(null);
    }
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (isProbing) return;
    if (e.dataTransfer.files?.[0]) handleFileUpload(e.dataTransfer.files[0]);
  };

  const tabButton = (value: 'local' | 'upload', label: string) => (
    <button
      onClick={() => setTab(value)}
      className={`px-3 py-1 rounded text-xs font-medium transition-all ${
        tab === value ? 'bg-indigo-600 text-white shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="bg-surface border border-line rounded-xl p-5 shadow-lg">
      <div className="flex items-center justify-between gap-4 border-b border-line pb-3 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-indigo-400" />
            {t('dropzone.title')}
          </h2>
          <p className="text-xs text-zinc-400 mt-0.5">{t('dropzone.subtitle')}</p>
        </div>
        <div className="flex bg-sunken p-1 rounded-lg border border-line flex-shrink-0">
          {tabButton('local', t('dropzone.tabLocal'))}
          {tabButton('upload', t('dropzone.tabUpload'))}
        </div>
      </div>

      {tab === 'local' ? (
        <form onSubmit={handleProbeLocalPath} className="space-y-3">
          <label className="block text-xs font-medium text-zinc-300">{t('dropzone.pathLabel')}</label>
          <div className="flex gap-2">
            <input
              type="text"
              dir="ltr"
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
              placeholder={t('dropzone.pathPlaceholder')}
              className="flex-1 bg-sunken border border-line rounded-lg px-3 py-2 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 font-mono"
            />
            <button
              type="submit"
              disabled={isProbing || !localPath.trim()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-xs font-semibold rounded-lg transition-colors flex items-center gap-2 cursor-pointer"
            >
              {isProbing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {t('dropzone.inspecting')}
                </>
              ) : (
                t('dropzone.inspect')
              )}
            </button>
          </div>
          <p className="text-[11px] text-zinc-500">{t('dropzone.pathHint')}</p>
        </form>
      ) : (
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
          className="border-2 border-dashed border-line hover:border-indigo-500/50 rounded-xl p-8 text-center bg-sunken/50 transition-colors flex flex-col items-center justify-center cursor-pointer"
          onClick={() => {
            if (isProbing) return;
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
          <p className="text-xs font-medium text-zinc-300">{t('dropzone.dropTitle')}</p>
          <p className="text-[11px] text-zinc-500 mt-1">{t('dropzone.dropHint')}</p>
          {isProbing && (
            <div className="mt-3 w-full max-w-sm space-y-1.5">
              <div className="flex items-center justify-center gap-2 text-xs text-indigo-400 font-medium">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {uploadProgress !== null && uploadProgress < 1
                  ? t('dropzone.uploading', { percent: Math.round(uploadProgress * 100) })
                  : t('dropzone.inspectingMedia')}
              </div>
              {uploadProgress !== null && (
                <div className="h-1.5 rounded-full bg-line overflow-hidden" dir="ltr">
                  <div className="h-full bg-indigo-500 transition-all" style={{ width: `${uploadProgress * 100}%` }} />
                </div>
              )}
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
            {t('dropzone.probed', {
              name: probeResult.file_name,
              size: Math.round(probeResult.file_size / (1024 * 1024)),
              duration: formatClock(probeResult.duration),
            })}
          </span>
        </div>
      )}
    </div>
  );
};

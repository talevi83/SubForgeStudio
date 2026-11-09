import React, { useCallback, useRef, useState } from 'react';
import { api } from '../../services/api';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { formatClock } from '../../utils/time';
import { pickLocalMediaFile, readClipboardText, resolveDroppedFilePath } from '../../utils/desktop';
import { PasteContextMenu } from '../common/PasteContextMenu';
import { Upload, HardDrive, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react';

export const MediaDropzone: React.FC = () => {
  const setProbeResult = useJobStore((s) => s.setProbeResult);
  const probeResult = useJobStore((s) => s.probeResult);
  const t = useT();
  const [localPath, setLocalPath] = useState('');
  const [isProbing, setIsProbing] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pathInputRef = useRef<HTMLInputElement | null>(null);
  const [pasteMenu, setPasteMenu] = useState<{ x: number; y: number } | null>(null);
  const closePasteMenu = useCallback(() => setPasteMenu(null), []);

  const probePathDirectly = async (rawPath: string) => {
    const cleaned = rawPath.trim().replace(/^["']+|["']+$/g, '');
    if (!cleaned) return;
    setLocalPath(cleaned);
    setIsProbing(true);
    setError(null);
    try {
      const result = await api.probePath(cleaned);
      setProbeResult(result);
      if (result.file_path && !result.is_upload) {
        setLocalPath(result.file_path);
      }
    } catch (err: any) {
      setError(err.message || t('dropzone.probeFailed'));
    } finally {
      setIsProbing(false);
    }
  };

  // Right-click on the path field offers only "Paste" (the text goes in at the cursor, like Ctrl+V)
  const pasteIntoPath = async () => {
    setPasteMenu(null);
    const text = (await readClipboardText())?.replace(/[\r\n]+/g, ' ').trim();
    if (text === undefined || text === null) {
      setError(t('dropzone.pasteFailed'));
      return;
    }
    if (!text) return;
    const input = pathInputRef.current;
    const start = input?.selectionStart ?? localPath.length;
    const end = input?.selectionEnd ?? localPath.length;
    const next = localPath.slice(0, start) + text + localPath.slice(end);
    setLocalPath(next);
    setError(null);
    requestAnimationFrame(() => {
      if (!input) return;
      input.focus();
      const caret = start + text.length;
      input.setSelectionRange(caret, caret);
    });
  };

  const handleProbeLocalPath = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!localPath.trim()) return;
    await probePathDirectly(localPath);
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

  const onDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (isProbing) return;

    const droppedFile = e.dataTransfer.files?.[0];
    if (droppedFile) {
      // Must call resolveDroppedFilePath synchronously before any await so dataTransfer.files is still live
      const pathPromise = resolveDroppedFilePath(droppedFile, e.dataTransfer);
      const resolvedPath = await pathPromise;
      if (resolvedPath) {
        await probePathDirectly(resolvedPath);
        return;
      }
      await handleFileUpload(droppedFile);
      return;
    }

    const droppedText = e.dataTransfer.getData('text/plain')?.trim();
    if (droppedText) {
      await probePathDirectly(droppedText);
    }
  };

  const handleBrowseClick = async () => {
    if (isProbing) return;
    const pickedPath = await pickLocalMediaFile();
    if (pickedPath !== null) {
      if (pickedPath.trim()) {
        await probePathDirectly(pickedPath);
      }
      return;
    }
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'video/*,audio/*,.mkv,.mp4,.avi,.mov';
    input.onchange = (e: any) => {
      if (e.target.files?.[0]) void handleFileUpload(e.target.files[0]);
    };
    input.click();
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!isDragging) setIsDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          setIsDragging(false);
        }
      }}
      onDrop={(e) => void onDrop(e)}
      className="bg-surface border border-line rounded-xl p-5 shadow-lg space-y-4"
    >
      <div className="border-b border-line pb-3">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-indigo-400" />
          {t('dropzone.title')}
        </h2>
        <p className="text-xs text-zinc-400 mt-0.5">{t('dropzone.subtitle')}</p>
      </div>

      <div
        onClick={() => void handleBrowseClick()}
        data-testid="media-dropzone-area"
        className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors flex flex-col items-center justify-center cursor-pointer ${
          isDragging
            ? 'border-indigo-500 bg-indigo-500/10'
            : 'border-line hover:border-indigo-500/50 bg-sunken/50'
        }`}
      >
        <Upload className="w-7 h-7 text-indigo-400 mb-2" />
        <p className="text-xs font-medium text-zinc-200">{t('dropzone.dropTitle')}</p>
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

      <form onSubmit={(e) => void handleProbeLocalPath(e)} className="space-y-2">
        <label className="block text-xs font-medium text-zinc-300">{t('dropzone.pathLabel')}</label>
        <div className="flex gap-2">
          <input
            ref={pathInputRef}
            type="text"
            dir="ltr"
            value={localPath}
            onChange={(e) => setLocalPath(e.target.value)}
            onContextMenu={(e) => {
              e.preventDefault();
              setPasteMenu({ x: e.clientX, y: e.clientY });
            }}
            data-testid="local-path-input"
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
        {pasteMenu && (
          <PasteContextMenu x={pasteMenu.x} y={pasteMenu.y} onPaste={() => void pasteIntoPath()} onClose={closePasteMenu} />
        )}
      </form>

      {error && (
        <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {probeResult && (
        <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs flex items-center gap-2">
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

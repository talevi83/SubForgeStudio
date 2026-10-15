import React, { useRef, useEffect, useState, useMemo, useCallback } from 'react';
import { desktopApi } from '../../utils/desktop';
import { useJobStore } from '../../store/useJobStore';
import { api } from '../../services/api';
import { isRtlLanguage } from '../../types';
import { findSegmentAt, formatClock, isTypingTarget } from '../../utils/time';
import { useT } from '../../i18n';
import { Film, Play, Pause, RotateCcw, Maximize, Minimize, AlertTriangle, Keyboard } from 'lucide-react';

export const VideoSyncPlayer: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const subtitleOverlayMode = useJobStore((s) => s.subtitleOverlayMode);
  const setSubtitleOverlayMode = useJobStore((s) => s.setSubtitleOverlayMode);
  const setVideoCurrentTime = useJobStore((s) => s.setVideoCurrentTime);
  const seekRequest = useJobStore((s) => s.seekRequest);
  const t = useT();

  const containerRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  // Component is keyed by job id (see App), so this initial state is per job
  const [duration, setDuration] = useState(activeJob?.video_duration || 0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);

  const jobId = activeJob?.id;
  const videoSrc = useMemo(() => (jobId ? api.getVideoStreamUrl(jobId) : ''), [jobId]);

  const segments = useMemo(
    () => [...(activeJob?.segments || [])].sort((a, b) => a.start_time - b.start_time),
    [activeJob?.segments]
  );
  const activeSeg = useMemo(() => {
    const i = findSegmentAt(segments, currentTime);
    return i >= 0 ? segments[i] : null;
  }, [segments, currentTime]);

  // Smooth time tracking while playing (timeupdate only fires ~4x per second).
  // The store is updated less often — it drives the table highlight, not the overlay.
  useEffect(() => {
    if (!isPlaying) return;
    let raf = 0;
    let lastStoreUpdate = 0;
    const tick = (now: number) => {
      const v = videoRef.current;
      if (v) {
        setCurrentTime(v.currentTime);
        if (now - lastStoreUpdate > 200) {
          lastStoreUpdate = now;
          setVideoCurrentTime(v.currentTime);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [isPlaying, setVideoCurrentTime]);

  // Seek only when explicitly requested (row click, shortcuts) — not on every edit.
  useEffect(() => {
    const v = videoRef.current;
    if (!seekRequest || !v) return;
    v.currentTime = seekRequest.time;
    setCurrentTime(seekRequest.time);
    setVideoCurrentTime(seekRequest.time);
    if (seekRequest.play) v.play().catch(() => {});
  }, [seekRequest, setVideoCurrentTime]);

  const syncTime = () => {
    const v = videoRef.current;
    if (!v) return;
    setCurrentTime(v.currentTime);
    setVideoCurrentTime(v.currentTime);
  };

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }, []);

  const seekBy = useCallback((delta: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(v.duration || Infinity, v.currentTime + delta));
    setCurrentTime(v.currentTime);
    setVideoCurrentTime(v.currentTime);
  }, [setVideoCurrentTime]);

  const toggleFullscreen = useCallback(() => {
    // Fullscreen the container, not the <video>, so the subtitle overlay stays visible
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else containerRef.current?.requestFullscreen().catch(() => {});
  }, []);

  useEffect(() => {
    const onChange = () => {
      const full = !!document.fullscreenElement;
      setIsFullscreen(full);
      // In the desktop window the player only fills the window; make the window itself follow
      desktopApi()?.set_fullscreen?.(full)?.catch(() => {});
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  // Keyboard shortcuts (ignored while typing in the editor)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      const v = videoRef.current;
      if (!v) return;
      const state = useJobStore.getState();
      if (state.insertDraft) return; // the "add a line" dialog is open
      const job = state.activeJob;

      // Target for [ and ]: the selected subtitle, else the one under the playhead
      const target = () => {
        if (!job) return undefined;
        if (state.selectedSegmentIndex !== null) {
          return job.segments.find((s) => s.index === state.selectedSegmentIndex);
        }
        const sorted = [...job.segments].sort((a, b) => a.start_time - b.start_time);
        const i = findSegmentAt(sorted, v.currentTime);
        return i >= 0 ? sorted[i] : undefined;
      };

      switch (e.key) {
        case ' ':
        case 'k':
          e.preventDefault();
          // A focused button would also "click" on Space keyup (e.g. Retranslate) — drop focus
          if (e.target instanceof HTMLButtonElement) e.target.blur();
          togglePlay();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          seekBy(e.shiftKey ? -0.1 : -2);
          break;
        case 'ArrowRight':
          e.preventDefault();
          seekBy(e.shiftKey ? 0.1 : 2);
          break;
        case 'f':
          toggleFullscreen();
          break;
        case 'n':
        case 'N':
          e.preventDefault();
          v.pause();
          state.openInsert({ time: v.currentTime });
          break;
        case '[': {
          const seg = target();
          if (seg?.id && v.currentTime < seg.end_time) {
            state.updateSegment(seg.id, { start_time: Number(v.currentTime.toFixed(3)) });
          }
          break;
        }
        case ']': {
          const seg = target();
          if (seg?.id && v.currentTime > seg.start_time) {
            state.updateSegment(seg.id, { end_time: Number(v.currentTime.toFixed(3)) });
          }
          break;
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [togglePlay, seekBy, toggleFullscreen]);

  const onVideoError = () => {
    const err = videoRef.current?.error;
    if (!err) return;
    setVideoError(
      err.code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED || err.code === MediaError.MEDIA_ERR_DECODE
        ? t('player.codecError')
        : t('player.loadError', { message: err.message || `error ${err.code}` })
    );
  };

  const isRtlTarget = isRtlLanguage(activeJob?.target_language);
  const showOriginal = subtitleOverlayMode === 'dual' || subtitleOverlayMode === 'original';
  const showTranslated = subtitleOverlayMode === 'dual' || subtitleOverlayMode === 'translated';

  const modeButton = (mode: 'dual' | 'translated' | 'original', label: string, title: string) => (
    <button
      onClick={() => setSubtitleOverlayMode(mode)}
      className={`px-2.5 py-1 rounded text-[11px] font-medium transition-all ${
        subtitleOverlayMode === mode ? 'bg-indigo-600 text-white shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
      }`}
      title={title}
    >
      {label}
    </button>
  );

  return (
    <div className="flex flex-col h-full bg-surface border border-line rounded-xl overflow-hidden shadow-xl">
      {/* Top Controls Bar */}
      <div className="px-4 py-2.5 bg-sunken border-b border-line flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Film className="w-4 h-4 text-indigo-400" />
          <span className="text-xs font-semibold text-zinc-200">{t('player.title')}</span>
        </div>
        <div className="flex bg-surface p-0.5 rounded-lg border border-line text-xs">
          {modeButton('dual', t('player.dual'), t('player.dualTitle'))}
          {modeButton('translated', t('player.translated'), t('player.translatedTitle'))}
          {modeButton('original', t('player.original'), t('player.originalTitle'))}
        </div>
      </div>

      {/* Video + overlay (this whole box goes fullscreen) */}
      <div
        ref={containerRef}
        className="relative flex-1 bg-black flex items-center justify-center overflow-hidden min-h-[300px]"
      >
        {videoSrc ? (
          <video
            ref={videoRef}
            src={videoSrc}
            onTimeUpdate={() => !isPlaying && syncTime()}
            onSeeked={syncTime}
            onLoadedMetadata={() => setDuration(videoRef.current?.duration || activeJob?.video_duration || 0)}
            onPlay={() => setIsPlaying(true)}
            onPause={() => {
              setIsPlaying(false);
              syncTime();
            }}
            onError={onVideoError}
            controls={false}
            playsInline
            className="w-full h-full object-contain cursor-pointer"
            onClick={togglePlay}
          />
        ) : (
          <div className="text-zinc-600 text-xs flex flex-col items-center gap-2">
            <Film className="w-8 h-8 opacity-40" />
            <span>{t('player.noVideo')}</span>
          </div>
        )}

        {videoError && (
          <div className="absolute inset-x-4 top-4 p-3 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{videoError}</span>
          </div>
        )}

        {activeSeg && (
          <div className="absolute bottom-6 inset-x-4 flex flex-col items-center pointer-events-none z-10">
            <div
              className={`max-w-[90%] bg-black/80 px-4 py-2 rounded-lg text-center space-y-1 ${
                isFullscreen ? 'text-2xl' : 'text-sm sm:text-base'
              }`}
            >
              {showOriginal && activeSeg.original_text && (
                <p
                  className={`font-medium leading-relaxed whitespace-pre-line ${
                    activeSeg.segment_type === 'ON_SCREEN' ? 'text-cyan-200 italic' : 'text-white'
                  }`}
                >
                  {activeSeg.original_text}
                </p>
              )}
              {showTranslated && activeSeg.translated_text && (
                <p
                  dir={isRtlTarget ? 'rtl' : 'ltr'}
                  className="text-amber-300 font-medium leading-relaxed whitespace-pre-line font-rtl-hebrew"
                >
                  {activeSeg.translated_text}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Transport: play, seek bar, time, fullscreen. Timelines stay left-to-right in RTL too. */}
      <div className="px-4 py-2.5 bg-sunken border-t border-line flex items-center gap-3" dir="ltr">
        <button
          onClick={togglePlay}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition-colors cursor-pointer"
          title={isPlaying ? t('player.pause') : t('player.play')}
        >
          {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 fill-zinc-200" />}
        </button>
        <button
          onClick={() => seekBy(-3)}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition-colors cursor-pointer text-xs flex items-center gap-1 px-2"
          title={t('player.back3')}
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span className="text-[10px] font-mono">-3s</span>
        </button>

        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={Math.min(currentTime, duration || 0)}
          onChange={(e) => {
            const v = videoRef.current;
            if (!v) return;
            v.currentTime = Number(e.target.value);
            setCurrentTime(v.currentTime);
          }}
          className="flex-1 accent-indigo-500 cursor-pointer"
          aria-label={t('player.seek')}
        />
        <span className="text-[11px] font-mono-time text-zinc-400 whitespace-nowrap">
          {formatClock(currentTime)} / {formatClock(duration)}
        </span>

        <div className="relative">
          <button
            onClick={() => setShowHelp((v) => !v)}
            className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors cursor-pointer"
            title={t('player.shortcuts')}
          >
            <Keyboard className="w-4 h-4" />
          </button>
          {showHelp && (
            <div
              className="absolute bottom-full end-0 mb-2 w-64 p-3 rounded-lg bg-surface border border-line shadow-xl text-[11px] text-zinc-300 space-y-1 z-20"
              dir="auto"
            >
              <div><kbd className="text-indigo-300">Space</kbd> / <kbd className="text-indigo-300">K</kbd> — {t('player.helpPlay')}</div>
              <div><kbd className="text-indigo-300">← →</kbd> — {t('player.helpSeek')}</div>
              <div><kbd className="text-indigo-300">[</kbd> — {t('player.helpStart')}</div>
              <div><kbd className="text-indigo-300">]</kbd> — {t('player.helpEnd')}</div>
              <div><kbd className="text-indigo-300">F</kbd> — {t('player.helpFullscreen')}</div>
              <div><kbd className="text-indigo-300">N</kbd> — {t('player.helpInsert')}</div>
              <div className="text-zinc-500 pt-1">{t('player.helpTarget')}</div>
            </div>
          )}
        </div>
        <button
          onClick={toggleFullscreen}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors cursor-pointer"
          title={t('player.fullscreen')}
        >
          {isFullscreen ? <Minimize className="w-4 h-4" /> : <Maximize className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
};

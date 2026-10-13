import React, { useRef, useEffect, useState, useMemo } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { api } from '../../services/api';
import { Film, Play, Pause, RotateCcw, Maximize } from 'lucide-react';

export const VideoSyncPlayer: React.FC = () => {
  const {
    activeJob,
    subtitleOverlayMode,
    setSubtitleOverlayMode,
    setVideoCurrentTime,
    selectedSegmentIndex,
  } = useJobStore();

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentOverlayText, setCurrentOverlayText] = useState<{
    original?: string;
    translated?: string;
  } | null>(null);

  const videoSrc = useMemo(() => {
    return activeJob ? api.getVideoStreamUrl(activeJob.id) : '';
  }, [activeJob?.id]);

  // Handle time update to determine which subtitle is currently active
  const handleTimeUpdate = () => {
    if (!videoRef.current || !activeJob) return;
    const time = videoRef.current.currentTime;
    setVideoCurrentTime(time);

    // Find segment active at this timestamp
    const activeSeg = activeJob.segments.find(
      (s) => time >= s.start_time && time <= s.end_time
    );

    if (activeSeg) {
      setCurrentOverlayText({
        original: activeSeg.original_text,
        translated: activeSeg.translated_text || undefined,
      });
    } else {
      setCurrentOverlayText(null);
    }
  };

  // Jump to selected segment start time if triggered from table click
  useEffect(() => {
    if (selectedSegmentIndex !== null && videoRef.current && activeJob) {
      const seg = activeJob.segments.find((s) => s.index === selectedSegmentIndex);
      if (seg) {
        videoRef.current.currentTime = Math.max(0, seg.start_time);
        videoRef.current.play().catch(() => {});
      }
    }
  }, [selectedSegmentIndex, activeJob]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      videoRef.current.play().catch(() => {});
      setIsPlaying(true);
    } else {
      videoRef.current.pause();
      setIsPlaying(false);
    }
  };

  const jumpBack = () => {
    if (!videoRef.current) return;
    videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime - 3);
  };

  const toggleFullscreen = () => {
    if (!videoRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      videoRef.current.requestFullscreen();
    }
  };

  const isRtlTarget = activeJob?.target_language?.toLowerCase() === 'hebrew' ||
    activeJob?.target_language?.toLowerCase() === 'arabic';

  return (
    <div className="flex flex-col h-full bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-xl">
      {/* Top Controls Bar */}
      <div className="px-4 py-2.5 bg-[#121214] border-b border-[#27272a] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Film className="w-4 h-4 text-indigo-400" />
          <span className="text-xs font-semibold text-zinc-200">Video Sync Preview</span>
        </div>

        {/* Overlay Mode Switcher */}
        <div className="flex bg-[#18181b] p-0.5 rounded-lg border border-[#27272a] text-xs">
          <button
            onClick={() => setSubtitleOverlayMode('dual')}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-all ${
              subtitleOverlayMode === 'dual'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
            title="Display both Original and Translated subtitles stacked"
          >
            Dual Stacked
          </button>
          <button
            onClick={() => setSubtitleOverlayMode('translated')}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-all ${
              subtitleOverlayMode === 'translated'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
            title="Display translated subtitles only"
          >
            Translated (RTL)
          </button>
          <button
            onClick={() => setSubtitleOverlayMode('original')}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-all ${
              subtitleOverlayMode === 'original'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
            title="Display original subtitles only"
          >
            Original
          </button>
        </div>
      </div>

      {/* Video Container with Custom High-Precision Subtitle Overlay */}
      <div className="relative flex-1 bg-black flex items-center justify-center overflow-hidden min-h-[300px]">
        {videoSrc ? (
          <video
            ref={videoRef}
            src={videoSrc}
            onTimeUpdate={handleTimeUpdate}
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            controls={false}
            playsInline
            className="w-full h-full object-contain cursor-pointer"
            onClick={togglePlay}
          />
        ) : (
          <div className="text-zinc-600 text-xs flex flex-col items-center gap-2">
            <Film className="w-8 h-8 opacity-40" />
            <span>No video stream loaded</span>
          </div>
        )}

        {/* Live Subtitle Overlay */}
        {currentOverlayText && (
          <div className="absolute bottom-6 inset-x-4 flex flex-col items-center pointer-events-none z-10 transition-all">
            <div className="max-w-[90%] bg-black/85 backdrop-blur-sm border border-white/10 px-4 py-2 rounded-lg text-center shadow-2xl space-y-1">
              {(subtitleOverlayMode === 'dual' || subtitleOverlayMode === 'original') &&
                currentOverlayText.original && (
                  <p className="text-white text-sm sm:text-base font-medium leading-relaxed drop-shadow-md whitespace-pre-line">
                    {currentOverlayText.original}
                  </p>
                )}

              {(subtitleOverlayMode === 'dual' || subtitleOverlayMode === 'translated') &&
                currentOverlayText.translated && (
                  <p
                    dir={isRtlTarget ? 'rtl' : 'ltr'}
                    className={`text-amber-300 text-sm sm:text-base font-medium leading-relaxed drop-shadow-md whitespace-pre-line font-rtl-hebrew`}
                  >
                    {currentOverlayText.translated}
                  </p>
                )}
            </div>
          </div>
        )}
      </div>

      {/* Bottom Transport Controls Bar */}
      <div className="px-4 py-2.5 bg-[#121214] border-t border-[#27272a] flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <button
            onClick={togglePlay}
            className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition-colors cursor-pointer"
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 fill-zinc-200" />}
          </button>
          <button
            onClick={jumpBack}
            className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition-colors cursor-pointer text-xs flex items-center gap-1 px-2"
            title="Jump back 3 seconds"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span className="text-[10px] font-mono">-3s</span>
          </button>
        </div>

        <button
          onClick={toggleFullscreen}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors cursor-pointer"
          title="Fullscreen"
        >
          <Maximize className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

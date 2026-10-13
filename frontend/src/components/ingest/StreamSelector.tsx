import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { Volume2, Film, Check } from 'lucide-react';

export const StreamSelector: React.FC = () => {
  const { probeResult, pipelineConfig, updatePipelineConfig } = useJobStore();

  if (!probeResult) return null;

  const audioStreams = probeResult.audio_streams || [];
  const videoStream = probeResult.video_streams?.[0];

  const formatDuration = (seconds: number) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m ${s}s`;
    return `${m}m ${s}s`;
  };

  return (
    <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-5 shadow-lg space-y-4">
      <div className="flex items-center justify-between border-b border-[#27272a] pb-3">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <Film className="w-4 h-4 text-indigo-400" />
          Media Stream Inspector
        </h2>
        <span className="text-xs text-zinc-400 bg-zinc-800/80 px-2.5 py-1 rounded-md font-mono">
          {probeResult.format_name.toUpperCase()}
        </span>
      </div>

      {/* Media Metrics Row */}
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div className="p-3 bg-[#121214] border border-[#27272a] rounded-lg">
          <span className="text-zinc-500 block mb-1">Duration</span>
          <span className="font-semibold text-zinc-200 font-mono-time">
            {formatDuration(probeResult.duration)}
          </span>
        </div>
        <div className="p-3 bg-[#121214] border border-[#27272a] rounded-lg">
          <span className="text-zinc-500 block mb-1">Resolution & Codec</span>
          <span className="font-semibold text-zinc-200">
            {videoStream?.width ? `${videoStream.width}x${videoStream.height}` : 'Audio Only'} (
            {videoStream?.codec_name || 'N/A'})
          </span>
        </div>
        <div className="p-3 bg-[#121214] border border-[#27272a] rounded-lg">
          <span className="text-zinc-500 block mb-1">File Size</span>
          <span className="font-semibold text-zinc-200 font-mono">
            {Math.round(probeResult.file_size / (1024 * 1024))} MB
          </span>
        </div>
      </div>

      {/* Audio Stream Picker */}
      <div className="space-y-2">
        <label className="text-xs font-medium text-zinc-300 flex items-center gap-1.5">
          <Volume2 className="w-3.5 h-3.5 text-indigo-400" />
          Target Audio Track for Transcription
        </label>

        {audioStreams.length === 0 ? (
          <p className="text-xs text-amber-400 bg-amber-500/10 p-2.5 rounded-lg border border-amber-500/20">
            Warning: No audio tracks detected in this container.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2">
            {audioStreams.map((stream) => {
              const isSelected = pipelineConfig.selected_audio_stream === stream.index;
              return (
                <div
                  key={stream.index}
                  onClick={() => updatePipelineConfig({ selected_audio_stream: stream.index })}
                  className={`p-3 rounded-lg border text-left flex items-center justify-between cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-indigo-600/15 border-indigo-500/60 text-white'
                      : 'bg-[#121214] border-[#27272a] hover:border-zinc-700 text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-6 h-6 rounded flex items-center justify-center text-xs font-bold ${
                        isSelected ? 'bg-indigo-600 text-white' : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      #{stream.index}
                    </div>
                    <div>
                      <div className="text-xs font-semibold flex items-center gap-2">
                        <span>{stream.title || `Audio Stream #${stream.index}`}</span>
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400 font-mono uppercase">
                          {stream.codec_name}
                        </span>
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-0.5">
                        Language: <strong className="text-zinc-400 uppercase">{stream.language}</strong> | Channels:{' '}
                        {stream.channels} ({stream.channel_layout || `${stream.channels}.0`})
                        {stream.sample_rate ? ` | ${stream.sample_rate} Hz` : ''}
                      </div>
                    </div>
                  </div>

                  {isSelected && <Check className="w-4 h-4 text-indigo-400 mr-1" />}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

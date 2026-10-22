import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { formatClock } from '../../utils/time';
import { Volume2, Film, Check, Captions } from 'lucide-react';

export const StreamSelector: React.FC = () => {
  const probeResult = useJobStore((s) => s.probeResult);
  const selectedStream = useJobStore((s) => s.pipelineConfig.selected_audio_stream);
  const updatePipelineConfig = useJobStore((s) => s.updatePipelineConfig);
  const t = useT();

  if (!probeResult) return null;

  const audioStreams = probeResult.audio_streams || [];
  const subtitleStreams = probeResult.subtitle_streams || [];
  const videoStream = probeResult.video_streams?.[0];

  const metric = (label: string, value: React.ReactNode) => (
    <div className="p-3 bg-sunken border border-line rounded-lg">
      <span className="text-zinc-500 block mb-1">{label}</span>
      <span className="font-semibold text-zinc-200" dir="ltr">
        {value}
      </span>
    </div>
  );

  return (
    <div className="bg-surface border border-line rounded-xl p-5 shadow-lg space-y-4">
      <div className="flex items-center justify-between border-b border-line pb-3">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <Film className="w-4 h-4 text-indigo-400" />
          {t('streams.title')}
        </h2>
        <span className="text-xs text-zinc-400 bg-zinc-800/80 px-2.5 py-1 rounded-md font-mono">
          {probeResult.format_name.toUpperCase()}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3 text-xs">
        {metric(t('streams.duration'), <span className="font-mono-time">{formatClock(probeResult.duration)}</span>)}
        {metric(
          t('streams.video'),
          videoStream?.width ? `${videoStream.width}x${videoStream.height} (${videoStream.codec_name})` : t('streams.noVideo')
        )}
        {metric(t('streams.size'), `${Math.round(probeResult.file_size / (1024 * 1024))} MB`)}
      </div>

      <div className="space-y-2">
        <label className="text-xs font-medium text-zinc-300 flex items-center gap-1.5">
          <Volume2 className="w-3.5 h-3.5 text-indigo-400" />
          {t('streams.audioTrack')}
        </label>

        {audioStreams.length === 0 ? (
          <p className="text-xs text-amber-400 bg-amber-500/10 p-2.5 rounded-lg border border-amber-500/20">
            {t('streams.noAudio')}
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2">
            {audioStreams.map((stream) => {
              const isSelected = selectedStream === stream.index;
              return (
                <div
                  key={stream.index}
                  onClick={() => updatePipelineConfig({ selected_audio_stream: stream.index })}
                  className={`p-3 rounded-lg border text-start flex items-center justify-between cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-indigo-600/15 border-indigo-500/60 text-zinc-50'
                      : 'bg-sunken border-line hover:border-zinc-700 text-zinc-300'
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
                        <span>{stream.title || t('streams.audioStream', { index: stream.index })}</span>
                        <span className="text-[10px] px-1.5 rounded bg-zinc-800 text-zinc-400 font-mono uppercase">
                          {stream.codec_name}
                        </span>
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-0.5">
                        {t('streams.language')}: <strong className="text-zinc-400 uppercase">{stream.language}</strong> |{' '}
                        {t('streams.channels')}: {stream.channels} ({stream.channel_layout || `${stream.channels}.0`})
                        {stream.sample_rate ? ` | ${stream.sample_rate} Hz` : ''}
                      </div>
                    </div>
                  </div>
                  {isSelected && <Check className="w-4 h-4 text-indigo-400 me-1" />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {subtitleStreams.length > 0 && (
        <div className="space-y-2">
          <label className="text-xs font-medium text-zinc-300 flex items-center gap-1.5">
            <Captions className="w-3.5 h-3.5 text-indigo-400" />
            {t('streams.subtitleTracks')}
          </label>
          <div className="flex flex-wrap gap-2">
            {subtitleStreams.map((s) => {
              const forced = s.forced || /forced|signs/i.test(s.title || '');
              const fullDialogue = s.text_based && !forced && !/commentary|director/i.test(s.title || '');
              return (
                <span
                  key={s.index}
                  className={`text-[11px] px-2 py-1 rounded-md border ${
                    forced
                      ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-300'
                      : fullDialogue
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                      : 'bg-sunken border-line text-zinc-400'
                  }`}
                  title={s.text_based ? t('streams.textSubs') : t('streams.imageSubs')}
                >
                  #{s.index} {s.title || s.language?.toUpperCase() || t('streams.subtitles')} · {s.codec_name}
                  {forced && <strong className="ms-1">{t('streams.forced')}</strong>}
                  {!s.text_based && <span className="ms-1 opacity-70">{t('streams.image')}</span>}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

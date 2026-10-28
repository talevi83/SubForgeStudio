import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { formatClock } from '../../utils/time';
import { Volume2, Film, Check, Captions } from 'lucide-react';

export const StreamSelector: React.FC = () => {
  const probeResult = useJobStore((s) => s.probeResult);
  const selectedStream = useJobStore((s) => s.pipelineConfig.selected_audio_stream);
  const useEmbeddedSubtitles = useJobStore((s) => s.pipelineConfig.use_embedded_subtitles !== false);
  const selectedSubtitleStream = useJobStore((s) => s.pipelineConfig.selected_subtitle_stream);
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
          <div className="flex items-center justify-between gap-2">
            <label className="text-xs font-medium text-zinc-300 flex items-center gap-1.5">
              <Captions className="w-3.5 h-3.5 text-indigo-400" />
              {t('streams.subtitleTracks')}
            </label>
            {subtitleStreams.some((s) => s.text_based && !s.forced && !/forced|signs|commentary|director/i.test(s.title || '')) && (
              <label className="flex items-center gap-2 cursor-pointer text-[11px] text-zinc-300 select-none">
                <span>{t('config.useEmbeddedTitle')}</span>
                <input
                  type="checkbox"
                  role="switch"
                  aria-checked={useEmbeddedSubtitles}
                  checked={useEmbeddedSubtitles}
                  onChange={(e) => updatePipelineConfig({ use_embedded_subtitles: e.target.checked })}
                  className="sr-only peer"
                />
                <span
                  className={`relative inline-flex h-4 w-7 flex-shrink-0 rounded-full transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-500 ${
                    useEmbeddedSubtitles ? 'bg-emerald-600' : 'bg-zinc-700'
                  }`}
                  aria-hidden
                >
                  <span
                    className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all ${
                      useEmbeddedSubtitles ? 'start-3.5' : 'start-0.5'
                    }`}
                  />
                </span>
              </label>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {subtitleStreams.map((s) => {
              const forced = s.forced || /forced|signs/i.test(s.title || '');
              const fullDialogue = s.text_based && !forced && !/commentary|director/i.test(s.title || '');
              const isSelectedTrack =
                useEmbeddedSubtitles &&
                fullDialogue &&
                (selectedSubtitleStream === s.index ||
                  (selectedSubtitleStream == null &&
                    subtitleStreams.find(
                      (x) => x.text_based && !x.forced && !/forced|signs|commentary|director/i.test(x.title || '')
                    )?.index === s.index));
              return (
                <button
                  type="button"
                  key={s.index}
                  disabled={!s.text_based || forced}
                  onClick={() => {
                    if (fullDialogue) {
                      updatePipelineConfig({
                        use_embedded_subtitles: true,
                        selected_subtitle_stream: s.index,
                      });
                    }
                  }}
                  className={`text-[11px] px-2.5 py-1 rounded-md border flex items-center gap-1 transition-all ${
                    forced
                      ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-300 cursor-default'
                      : isSelectedTrack
                      ? 'bg-emerald-500/20 border-emerald-500/60 text-emerald-200 ring-1 ring-emerald-500/40 cursor-pointer'
                      : fullDialogue
                      ? 'bg-sunken border-line hover:border-emerald-500/40 text-zinc-300 cursor-pointer'
                      : 'bg-sunken border-line text-zinc-400 cursor-default'
                  }`}
                  title={s.text_based ? t('streams.textSubs') : t('streams.imageSubs')}
                >
                  <span>
                    #{s.index} {s.title || s.language?.toUpperCase() || t('streams.subtitles')}
                    {s.language && s.title && !s.title.toLowerCase().includes(s.language.toLowerCase())
                      ? ` (${s.language.toUpperCase()})`
                      : ''}{' '}
                    · {s.codec_name}
                  </span>
                  {forced && <strong className="ms-1">{t('streams.forced')}</strong>}
                  {!s.text_based && <span className="ms-1 opacity-70">{t('streams.image')}</span>}
                  {isSelectedTrack && <Check className="w-3 h-3 text-emerald-400 ms-0.5" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

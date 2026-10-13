import React, { useState } from 'react';
import { useJobStore } from '../../store/useJobStore';
import {
  Sparkles,
  Settings,
  Mic,
  Key,
  Play,
  Loader2,
  FileText,
} from 'lucide-react';

export const PipelineConfig: React.FC = () => {
  const {
    pipelineConfig,
    updatePipelineConfig,
    createAndStartJob,
    probeResult,
    systemInfo,
    isLoading,
  } = useJobStore();

  const [showKeyOverride, setShowKeyOverride] = useState(false);
  const isGeminiReady = systemInfo?.is_gemini_configured;

  if (!probeResult) return null;

  return (
    <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-5 shadow-lg space-y-6">
      <div className="flex items-center justify-between border-b border-[#27272a] pb-3">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <Settings className="w-4 h-4 text-indigo-400" />
          AI Pipeline Configuration
        </h2>
        <span className="text-xs text-zinc-400">Speech-to-Text & Translation Engines</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Transcription Settings Card */}
        <div className="space-y-4 p-4 rounded-xl bg-[#121214] border border-[#27272a]">
          <div className="flex items-center gap-2 text-xs font-semibold text-zinc-200 border-b border-[#27272a] pb-2">
            <Mic className="w-4 h-4 text-indigo-400" />
            Local Faster-Whisper Settings
          </div>

          <div className="space-y-3 text-xs">
            <div>
              <label className="block text-zinc-400 mb-1 font-medium">Whisper Model Weight</label>
              <select
                value={pipelineConfig.whisper_model}
                onChange={(e) => updatePipelineConfig({ whisper_model: e.target.value })}
                className="w-full bg-[#18181b] border border-[#27272a] rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:border-indigo-500"
              >
                <option value="tiny">tiny (Fastest, minimal RAM)</option>
                <option value="base">base (Fast, low RAM)</option>
                <option value="small">small (Balanced)</option>
                <option value="medium">medium (Recommended, high accuracy)</option>
                <option value="large-v3">large-v3 (Maximum precision)</option>
              </select>
            </div>

            <div>
              <label className="block text-zinc-400 mb-1 font-medium">Source Spoken Language</label>
              <select
                value={pipelineConfig.source_language}
                onChange={(e) => updatePipelineConfig({ source_language: e.target.value })}
                className="w-full bg-[#18181b] border border-[#27272a] rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:border-indigo-500"
              >
                <option value="auto">Auto-detect Language</option>
                <option value="en">English (en)</option>
                <option value="ja">Japanese (ja)</option>
                <option value="he">Hebrew (he)</option>
                <option value="es">Spanish (es)</option>
                <option value="fr">French (fr)</option>
                <option value="de">German (de)</option>
                <option value="it">Italian (it)</option>
                <option value="ko">Korean (ko)</option>
                <option value="zh">Chinese (zh)</option>
                <option value="ru">Russian (ru)</option>
              </select>
            </div>

            <div className="pt-1 flex items-center justify-between">
              <div>
                <span className="text-zinc-300 font-medium block">Silero VAD Voice Activity Filter</span>
                <span className="text-[11px] text-zinc-500 block">
                  Discards background silence & music hallucinations
                </span>
              </div>
              <input
                type="checkbox"
                checked={pipelineConfig.vad_filter}
                onChange={(e) => updatePipelineConfig({ vad_filter: e.target.checked })}
                className="w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800 cursor-pointer"
              />
            </div>
          </div>
        </div>

        {/* Translation Settings Card */}
        <div className="space-y-4 p-4 rounded-xl bg-[#121214] border border-[#27272a]">
          <div className="flex items-center justify-between border-b border-[#27272a] pb-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-zinc-200">
              <Sparkles className="w-4 h-4 text-indigo-400" />
              Gemini Context-Aware Translation
            </div>

            <div className="flex items-center gap-2">
              <label className="text-[11px] text-zinc-400">Enable</label>
              <input
                type="checkbox"
                checked={pipelineConfig.enable_translation}
                onChange={(e) => updatePipelineConfig({ enable_translation: e.target.checked })}
                className="w-4 h-4 rounded border-zinc-700 text-indigo-600 focus:ring-indigo-500 bg-zinc-800 cursor-pointer"
              />
            </div>
          </div>

          {pipelineConfig.enable_translation ? (
            <div className="space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">Target Language</label>
                  <select
                    value={pipelineConfig.target_language}
                    onChange={(e) => updatePipelineConfig({ target_language: e.target.value })}
                    className="w-full bg-[#18181b] border border-[#27272a] rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="Hebrew">Hebrew (עברית - BiDi / RTL)</option>
                    <option value="English">English</option>
                    <option value="Spanish">Spanish (Español)</option>
                    <option value="French">French (Français)</option>
                    <option value="German">German (Deutsch)</option>
                    <option value="Japanese">Japanese (日本語)</option>
                    <option value="Arabic">Arabic (العربية - RTL)</option>
                    <option value="Russian">Russian (Русский)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">Gemini Model</label>
                  <select
                    value={pipelineConfig.gemini_model}
                    onChange={(e) => updatePipelineConfig({ gemini_model: e.target.value })}
                    className="w-full bg-[#18181b] border border-[#27272a] rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="gemini-2.5-flash">gemini-2.5-flash (Fast & Accurate)</option>
                    <option value="gemini-2.5-pro">gemini-2.5-pro (Deep Reasoning)</option>
                  </select>
                </div>
              </div>

              {/* API Key Status & Override */}
              <div className="p-2.5 rounded-lg bg-[#18181b] border border-[#27272a] flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-zinc-300">
                    <Key className="w-3.5 h-3.5 text-zinc-400" />
                    <span className="text-[11px]">GEMINI_API_KEY:</span>
                    <span
                      className={`text-[10px] font-semibold px-1.5 py-0.2 rounded ${
                        isGeminiReady
                          ? 'bg-emerald-500/15 text-emerald-400'
                          : 'bg-amber-500/15 text-amber-400'
                      }`}
                    >
                      {isGeminiReady ? 'Loaded from backend/.env' : 'Missing from .env'}
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={() => setShowKeyOverride(!showKeyOverride)}
                    className="text-[10px] text-indigo-400 hover:text-indigo-300 underline cursor-pointer"
                  >
                    {showKeyOverride ? 'Hide Override' : 'Override Key'}
                  </button>
                </div>

                {showKeyOverride && (
                  <input
                    type="password"
                    placeholder="Enter runtime Gemini API key (AIza...)"
                    value={pipelineConfig.gemini_api_key_override || ''}
                    onChange={(e) =>
                      updatePipelineConfig({ gemini_api_key_override: e.target.value })
                    }
                    className="w-full bg-[#121214] border border-[#27272a] rounded px-2.5 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 font-mono"
                  />
                )}
              </div>

              {/* Show / Movie Context Notes */}
              <div>
                <label className="block text-zinc-400 mb-1 font-medium flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5 text-zinc-400" />
                  Show / Movie Context Notes (Optional)
                </label>
                <textarea
                  rows={2}
                  value={pipelineConfig.context_notes || ''}
                  onChange={(e) => updatePipelineConfig({ context_notes: e.target.value })}
                  placeholder="e.g. Sci-fi thriller. Sarah speaks in feminine singular. Keep military jargon authentic."
                  className="w-full bg-[#18181b] border border-[#27272a] rounded-lg p-2 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 resize-none"
                />
              </div>
            </div>
          ) : (
            <div className="py-6 text-center text-zinc-500 text-xs">
              Translation step disabled. The output will contain original transcribed subtitles only.
            </div>
          )}
        </div>
      </div>

      {/* Launch CTA */}
      <div className="pt-2">
        <button
          onClick={createAndStartJob}
          disabled={isLoading}
          className="w-full py-3 px-6 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:opacity-60 text-white font-bold text-sm shadow-lg shadow-indigo-600/30 flex items-center justify-center gap-2 cursor-pointer transition-all active:scale-[0.99]"
        >
          {isLoading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Initializing Processing Pipeline...
            </>
          ) : (
            <>
              <Play className="w-4 h-4 fill-white" />
              Launch Subtitling Pipeline
            </>
          )}
        </button>
      </div>
    </div>
  );
};

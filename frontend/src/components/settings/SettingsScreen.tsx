import React, { useEffect, useMemo, useState } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { useUiStore } from '../../store/useUiStore';
import type { Language, Theme } from '../../store/useUiStore';
import { useJobStore } from '../../store/useJobStore';
import { useT, formatUsd, formatNumber, formatRate } from '../../i18n';
import type { MessageKey } from '../../i18n';
import { useCostEstimate } from '../../hooks/useCostEstimate';
import { thinkingLabel, modelLabel } from './labels';
import type {
  AppSettings, CatalogModel, GeminiCallSettings, MediaResolution, ModelCheckResponse, ModelCheckResult, OllamaStatus, OnscreenPosition, PipelineTask, ThinkingLevel,
} from '../../types';
import { api } from '../../services/api';
import { StorageSection } from './StorageSection';
import { ApiKeySection } from './ApiKeySection';
import {
  ArrowLeft, Brain, Coins, Languages, Moon, Palette, Sun, Save, Loader2, Check, ExternalLink, PlugZap, CheckCircle2, XCircle,
  Hourglass, ScrollText, Stethoscope, Download, Cpu, Terminal,
} from 'lucide-react';


const card = 'bg-surface border border-line rounded-xl p-5 shadow-lg space-y-4';
const selectClass =
  'w-full bg-sunken border border-line rounded-lg px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500';

type Step = PipelineTask;
const STEPS: Step[] = ['translation', 'glossary', 'vision'];

/** Per-step check results; older servers only report per model. */
function checksByStep(res: ModelCheckResponse | undefined, target: AppSettings): Partial<Record<Step, ModelCheckResult>> {
  const out: Partial<Record<Step, ModelCheckResult>> = {};
  for (const step of STEPS) {
    const r = res?.steps?.[step] ?? res?.results?.[target[step].model];
    if (r) out[step] = { ok: r.ok, error: r.error };
  }
  return out;
}

const Segmented = <V extends string>({
  value,
  options,
  onChange,
}: {
  value: V;
  options: { value: V; label: React.ReactNode }[];
  onChange: (v: V) => void;
}) => (
  <div className="inline-flex bg-sunken p-1 rounded-lg border border-line" role="radiogroup">
    {options.map((o) => (
      <button
        key={o.value}
        role="radio"
        aria-checked={value === o.value}
        onClick={() => onChange(o.value)}
        className={`px-3 py-1.5 rounded text-xs font-medium transition-all flex items-center gap-1.5 cursor-pointer ${
          value === o.value ? 'bg-indigo-600 text-white shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
        }`}
      >
        {o.label}
      </button>
    ))}
  </div>
);

const PriceLine: React.FC<{ model?: CatalogModel; flex?: boolean }> = ({ model, flex }) => {
  const t = useT();
  const language = useUiStore((s) => s.language);
  if (!model) return <p className="text-[11px] text-amber-300">{t('settings.priceUnknown')}</p>;
  // Flex bills every token at a fixed share of the Standard price
  const factor = flex && model.flex_price_factor ? model.flex_price_factor : 1;
  const usd = (v: number) => formatRate(v * factor, language);
  return (
    <div className="space-y-0.5 text-[11px]">
      <p className="text-zinc-300">
        {factor !== 1 && <span className="text-emerald-400 font-medium">{t('settings.flexTag')}: </span>}
        {t('settings.price', { input: usd(model.input_per_m), output: usd(model.output_per_m) })}
      </p>
      {model.input_long_per_m && model.output_long_per_m ? (
        <p className="text-zinc-500">
          {t('settings.priceLong', { input: usd(model.input_long_per_m), output: usd(model.output_long_per_m) })}
        </p>
      ) : null}
      {model.price_change && (
        <p className="text-amber-300">
          {t('settings.priceChange', {
            input: usd(model.price_change.input_per_m),
            output: usd(model.price_change.output_per_m),
            date: new Date(model.price_change.after + 'T12:00:00').toLocaleDateString(language === 'he' ? 'he-IL' : 'en-US', {
              day: 'numeric',
              month: 'short',
              year: 'numeric',
            }),
          })}
        </p>
      )}
      {model.note && <p className="text-zinc-500">{model.note}</p>}
    </div>
  );
};

const FlexToggle: React.FC<{
  step: Step;
  value: GeminiCallSettings;
  info?: CatalogModel;
  onChange: (v: GeminiCallSettings) => void;
}> = ({ step, value, info, onChange }) => {
  const t = useT();
  const supported = info?.supports_flex !== false;
  const on = !!value.flex && supported;
  return (
    <div className="space-y-1">
      <label
        className={`inline-flex items-center gap-2 text-[11px] font-medium ${
          supported ? 'text-zinc-300 cursor-pointer' : 'text-zinc-600 cursor-not-allowed'
        }`}
      >
        <input
          type="checkbox"
          role="switch"
          data-testid={`model-card-${step}-flex`}
          checked={on}
          disabled={!supported}
          onChange={(e) => onChange({ ...value, flex: e.target.checked })}
          className="accent-emerald-500"
        />
        <Hourglass className="w-3 h-3" />
        {t('settings.flex')}
        <span className="text-[10px] px-1.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
          {t('settings.flexSaving')}
        </span>
      </label>
      {!supported ? (
        <p className="text-[11px] text-zinc-500">{t('settings.flexUnsupported')}</p>
      ) : on ? (
        <p className="text-[11px] text-amber-300">
          {t(step === 'translation' ? 'settings.flexHintTranslation' : 'settings.flexHint')}
        </p>
      ) : null}
    </div>
  );
};

const ModelCard: React.FC<{
  step: Step;
  title: string;
  hint: string;
  value: GeminiCallSettings;
  catalog: CatalogModel[];
  onChange: (v: GeminiCallSettings) => void;
  children?: React.ReactNode;
  check?: ModelCheckResult;
}> = ({ step, title, hint, value, catalog, onChange, children, check }) => {
  const t = useT();
  const testId = `model-card-${step}`;
  // Only models that can do this step's job (e.g. image input for vision, strong multilingual output for translation)
  const options = catalog.filter((m) => !m.tasks || m.tasks.includes(step));
  const info = catalog.find((m) => m.id === value.model);
  const levels: ThinkingLevel[] = info ? info.thinking : ['default'];

  return (
    <div className="p-4 rounded-xl bg-sunken border border-line space-y-3" data-testid={testId}>
      <div>
        <h3 className="text-xs font-semibold text-zinc-100 flex items-center gap-2">
          {title}
          {info?.preview && (
            <span className="text-[10px] px-1.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
              {t('settings.preview')}
            </span>
          )}
        </h3>
        <p className="text-[11px] text-zinc-500 mt-0.5">{hint}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <label className="text-[11px] text-zinc-400 space-y-1">
          <span className="block font-medium">{t('settings.model')}</span>
          <select
            value={value.model}
            aria-label={`${title} — ${t('settings.model')}`}
            onChange={(e) => {
              const next = catalog.find((m) => m.id === e.target.value);
              // Keep the thinking level (and Flex) if the new model supports it
              const thinking = next?.thinking.includes(value.thinking) ? value.thinking : 'default';
              onChange({ model: e.target.value, thinking, flex: !!value.flex && next?.supports_flex !== false });
            }}
            className={selectClass}
          >
            {options.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
            {!options.some((m) => m.id === value.model) && (
              // A custom model set in .env — keep it visible instead of silently showing another one
              <option value={value.model}>{value.model}</option>
            )}
          </select>
        </label>
        <label className="text-[11px] text-zinc-400 space-y-1">
          <span className="block font-medium flex items-center gap-1">
            <Brain className="w-3 h-3" />
            {t('settings.thinking')}
          </span>
          <select
            value={value.thinking}
            aria-label={`${title} — ${t('settings.thinking')}`}
            onChange={(e) => onChange({ ...value, thinking: e.target.value as ThinkingLevel })}
            className={selectClass}
            disabled={levels.length === 1}
          >
            {levels.map((l) => (
              <option key={l} value={l}>
                {thinkingLabel(t, l, info)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <FlexToggle step={step} value={value} info={info} onChange={onChange} />
      <PriceLine model={info} flex={value.flex} />
      {check && (
        <p
          className={`text-[11px] flex items-start gap-1.5 ${check.ok ? 'text-emerald-400' : 'text-rose-400'}`}
          data-testid={`${testId}-check`}
        >
          {check.ok ? <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" /> : <XCircle className="w-3.5 h-3.5 flex-shrink-0" />}
          <span className="break-all">{check.ok ? t('settings.modelOk') : t('settings.modelFailed', { error: check.error || '' })}</span>
        </p>
      )}
      {children}
    </div>
  );
};

export const SettingsScreen: React.FC = () => {
  const t = useT();
  const language = useUiStore((s) => s.language);
  const theme = useUiStore((s) => s.theme);
  const setLanguage = useUiStore((s) => s.setLanguage);
  const setTheme = useUiStore((s) => s.setTheme);
  const setView = useUiStore((s) => s.setView);
  const { settings, catalog, loaded, error, load, save, pricingChecked, pricingSource } = useSettingsStore();

  const [draft, setDraft] = useState<AppSettings | null>(settings);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [minutes, setMinutes] = useState(45);
  const [withTranslation, setWithTranslation] = useState(true);
  const [withOnscreen, setWithOnscreen] = useState(true);
  const [checks, setChecks] = useState<Partial<Record<Step, ModelCheckResult>>>({});
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
  const [ollamaBusy, setOllamaBusy] = useState(false);
  const keyOverride = useJobStore((s) => s.pipelineConfig.gemini_api_key_override);

  const runCheck = async (target: AppSettings | null = draft) => {
    if (!target) return;
    setChecking(true);
    setCheckError(null);
    try {
      setChecks(checksByStep(await api.checkModels(target, keyOverride), target));
    } catch (e: any) {
      setCheckError(e.message);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    if (!loaded) load();
  }, [loaded, load]);

  useEffect(() => {
    let active = true;
    api.getOllamaStatus()
      .then((res) => {
        if (active) setOllamaStatus(res);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!ollamaStatus?.installing) return;
    const timer = window.setInterval(async () => {
      try {
        const res = await api.getOllamaStatus();
        setOllamaStatus(res);
        if (!res.installing) {
          setOllamaBusy(false);
        }
      } catch {
        // Ignore transient polling errors
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [ollamaStatus?.installing]);

  const onSetupOllama = async () => {
    setOllamaBusy(true);
    try {
      const res = await api.setupOllama();
      setOllamaStatus(res);
      if (!res.installing) {
        setOllamaBusy(false);
      }
    } catch (e: any) {
      setOllamaBusy(false);
      setOllamaStatus((prev) =>
        prev
          ? { ...prev, installing: false, error: e.message }
          : {
              installed: false,
              running: false,
              models: [],
              selected_model: null,
              recommended_model: 'qwen2.5:7b',
              installing: false,
              step: 'error',
              error: e.message,
              download_url: 'https://ollama.com/download',
            }
      );
    }
  };

  // Adopt server settings when they (re)load, unless the user is editing
  const [syncedFrom, setSyncedFrom] = useState<AppSettings | null>(null);
  if (settings && settings !== syncedFrom) {
    setSyncedFrom(settings);
    setDraft(settings);
  }

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(settings), [draft, settings]);

  const estimate = useCostEstimate(
    draft
      ? {
          duration_seconds: Math.max(1, minutes) * 60,
          enable_translation: withTranslation,
          detect_onscreen_text: withOnscreen,
          cost_saver_mode: !!draft.cost_saver_mode,
          settings: draft,
        }
      : null
  );

  const update = (step: Step, value: GeminiCallSettings) => {
    if (!draft) return;
    setDraft({ ...draft, [step]: value });
    // A result for the previous model would be misleading
    setChecks(({ [step]: _old, ...rest }) => rest);
  };
  const visionInfo = catalog.find((m) => m.id === draft?.vision.model);

  const onSave = async () => {
    if (!draft) return;
    setSaving(true);
    const ok = await save(draft);
    setSaving(false);
    if (ok) {
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
      runCheck(draft); // confirm right away that the saved models actually work with this key
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-10">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-50">{t('settings.title')}</h1>
        <button
          onClick={() => setView('studio')}
          className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200 cursor-pointer"
        >
          <ArrowLeft className="w-3.5 h-3.5 rtl:rotate-180" />
          {t('settings.back')}
        </button>
      </div>

      {/* Appearance */}
      <section className={card}>
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2 border-b border-line pb-3">
          <Palette className="w-4 h-4 text-indigo-400" />
          {t('settings.appearance')}
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div className="space-y-2">
            <span className="flex items-center gap-1.5 text-zinc-400 font-medium">
              <Languages className="w-3.5 h-3.5" />
              {t('settings.language')}
            </span>
            <Segmented<Language>
              value={language}
              onChange={setLanguage}
              options={[
                { value: 'he', label: 'עברית' },
                { value: 'en', label: 'English' },
              ]}
            />
          </div>
          <div className="space-y-2">
            <span className="flex items-center gap-1.5 text-zinc-400 font-medium">
              <Palette className="w-3.5 h-3.5" />
              {t('settings.theme')}
            </span>
            <Segmented<Theme>
              value={theme}
              onChange={setTheme}
              options={[
                { value: 'dark', label: <><Moon className="w-3.5 h-3.5" />{t('settings.dark')}</> },
                { value: 'light', label: <><Sun className="w-3.5 h-3.5" />{t('settings.light')}</> },
              ]}
            />
          </div>
        </div>
      </section>

      {/* Gemini API Key (foldable / collapsible above AI models) */}
      <ApiKeySection />

      {/* AI models */}
      <section className={card}>
        <div className="border-b border-line pb-3">
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <Brain className="w-4 h-4 text-indigo-400" />
            {t('settings.aiModels')}
          </h2>
          <p className="text-[11px] text-zinc-400 mt-1">{t('settings.aiModelsHint')}</p>
        </div>

        {!draft ? (
          <div className="text-xs text-zinc-400 flex items-center gap-2">
            {error ? t('settings.loadFailed') : <Loader2 className="w-4 h-4 animate-spin" />}
          </div>
        ) : (
          <div className="space-y-3">
            <ModelCard
              step="translation"
              title={t('settings.stepTranslation')}
              hint={t('settings.stepTranslationHint')}
              value={draft.translation}
              catalog={catalog}
              onChange={(v) => update('translation', v)}
              check={checks.translation}
            />
            <ModelCard
              step="glossary"
              title={t('settings.stepGlossary')}
              hint={t('settings.stepGlossaryHint')}
              value={draft.glossary}
              catalog={catalog}
              onChange={(v) => update('glossary', v)}
              check={checks.glossary}
            />
            <ModelCard
              step="vision"
              title={t('settings.stepVision')}
              hint={t('settings.stepVisionHint')}
              value={draft.vision}
              catalog={catalog}
              onChange={(v) => update('vision', v)}
              check={checks.vision}
            >
              {visionInfo && !visionInfo.supports_media_resolution ? null : (
                <div className="space-y-1.5">
                  <span className="block text-[11px] text-zinc-400 font-medium">{t('settings.frameDetail')}</span>
                  <Segmented<MediaResolution>
                    value={draft.vision_media_resolution}
                    onChange={(v) => setDraft({ ...draft, vision_media_resolution: v })}
                    options={(['low', 'medium', 'high'] as MediaResolution[]).map((r) => ({
                      value: r,
                      label: t(`settings.resolution.${r}` as MessageKey),
                    }))}
                  />
                  <p className="text-[11px] text-zinc-500">{t('settings.frameDetailHint')}</p>
                </div>
              )}

              <div className="space-y-2 pt-3 border-t border-line/40">
                <div className="flex items-center justify-between">
                  <span className="block text-[11px] text-zinc-300 font-medium">
                    {t('settings.onscreenPosition')}
                  </span>
                  <span className="text-[11px] font-semibold text-indigo-400">
                    {t(`settings.pos.${draft.onscreen_position || 'top_center'}` as MessageKey)}
                  </span>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                  {(
                    [
                      { id: 'top_center', icon: '⬆️' },
                      { id: 'middle_right', icon: '➡️' },
                      { id: 'middle_left', icon: '⬅️' },
                      { id: 'top_right', icon: '↗️' },
                      { id: 'top_left', icon: '↖️' },
                    ] as const satisfies readonly { id: OnscreenPosition; icon: string }[]
                  ).map((p) => {
                    const active = (draft.onscreen_position || 'top_center') === p.id;
                    return (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() => setDraft({ ...draft, onscreen_position: p.id })}
                        className={`px-2.5 py-1.5 rounded-lg border text-xs flex items-center gap-1.5 transition-all cursor-pointer ${
                          active
                            ? 'bg-indigo-600 border-indigo-500 text-white font-medium shadow-sm'
                            : 'bg-sunken border-line text-zinc-400 hover:text-zinc-200 hover:border-zinc-600'
                        }`}
                      >
                        <span className="text-sm">{p.icon}</span>
                        <span className="truncate">{t(`settings.pos.${p.id}` as MessageKey)}</span>
                      </button>
                    );
                  })}
                </div>
                <p className="text-[11px] text-zinc-500">{t('settings.onscreenPositionHint')}</p>
              </div>
            </ModelCard>

            <div
              className={`rounded-xl border transition-colors ${
                draft.convert_to_metric
                  ? 'bg-indigo-500/10 border-indigo-500/40'
                  : 'bg-sunken border-line hover:border-zinc-600'
              }`}
            >
              <label
                className="flex items-start gap-3 p-4 cursor-pointer"
                data-testid="metric-conversion-toggle"
              >
                <span className="flex-1 min-w-0">
                  <span className="block text-xs font-semibold text-zinc-100 flex items-center gap-1.5">
                    <span>📏</span>
                    <span>{t('settings.metric.label')}</span>
                  </span>
                  <span className="block text-[11px] text-zinc-400 mt-0.5 leading-relaxed">
                    {t('settings.metric.description')}
                  </span>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  data-testid="metric-conversion-switch"
                  aria-checked={!!draft.convert_to_metric}
                  checked={!!draft.convert_to_metric}
                  onChange={(e) => setDraft({ ...draft, convert_to_metric: e.target.checked })}
                  className="sr-only peer"
                />
                <span
                  className={`relative mt-0.5 inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-500 ${
                    draft.convert_to_metric ? 'bg-indigo-600' : 'bg-zinc-700'
                  }`}
                  aria-hidden
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
                      draft.convert_to_metric ? 'start-4.5' : 'start-0.5'
                    }`}
                  />
                </span>
              </label>
            </div>

            <div
              className={`rounded-xl border transition-colors ${
                draft.cost_saver_mode
                  ? 'bg-emerald-500/10 border-emerald-500/40'
                  : 'bg-sunken border-line hover:border-zinc-600'
              }`}
            >
              <label
                className="flex items-start gap-3 p-4 cursor-pointer"
                data-testid="cost-saver-toggle"
              >
                <span className="flex-1 min-w-0">
                  <span className="block text-xs font-semibold text-zinc-100">
                    {t('settings.costSaver.label')}
                  </span>
                  <span className="block text-[11px] text-zinc-400 mt-0.5 leading-relaxed">
                    {t('settings.costSaver.description')}
                  </span>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  data-testid="cost-saver-switch"
                  aria-checked={!!draft.cost_saver_mode}
                  checked={!!draft.cost_saver_mode}
                  onChange={(e) => setDraft({ ...draft, cost_saver_mode: e.target.checked })}
                  className="sr-only peer"
                />
                <span
                  className={`relative mt-0.5 inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-500 ${
                    draft.cost_saver_mode ? 'bg-emerald-600' : 'bg-zinc-700'
                  }`}
                  aria-hidden
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
                      draft.cost_saver_mode ? 'start-4.5' : 'start-0.5'
                    }`}
                  />
                </span>
              </label>

              {/* Local AI (Ollama) Setup & Explanation */}
              <div
                className="px-4 pb-4 pt-3 border-t border-line/70 space-y-3 text-[11px]"
                data-testid="ollama-setup-panel"
              >
                <div className="space-y-1">
                  <div className="font-semibold text-zinc-200 flex items-center gap-1.5">
                    <Cpu className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                    <span>{t('settings.costSaver.whyTitle')}</span>
                  </div>
                  <p className="text-zinc-400 leading-relaxed">
                    {t('settings.costSaver.whyText')}
                  </p>
                </div>

                {/* Live Ollama status & one-click install button */}
                <div className="flex flex-wrap items-center justify-between gap-2.5 bg-surface/70 border border-line rounded-lg p-2.5">
                  <div className="flex items-center gap-2 min-w-0">
                    {ollamaStatus?.running && ollamaStatus?.selected_model ? (
                      <>
                        <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
                        <span className="text-emerald-300 font-medium truncate">
                          {t('settings.costSaver.statusReady', { model: ollamaStatus.selected_model })}
                        </span>
                      </>
                    ) : ollamaStatus?.installed ? (
                      <>
                        <Hourglass className="w-4 h-4 text-amber-400 flex-shrink-0" />
                        <span className="text-amber-300">
                          {t('settings.costSaver.statusInstalledNoModel')}
                        </span>
                      </>
                    ) : (
                      <>
                        <Cpu className="w-4 h-4 text-zinc-400 flex-shrink-0" />
                        <span className="text-zinc-300">
                          {t('settings.costSaver.statusNotInstalled')}
                        </span>
                      </>
                    )}
                  </div>

                  {(!ollamaStatus?.running || !ollamaStatus?.selected_model || ollamaStatus?.installing || ollamaBusy) && (
                    <button
                      type="button"
                      onClick={onSetupOllama}
                      disabled={ollamaBusy || !!ollamaStatus?.installing}
                      data-testid="ollama-setup-btn"
                      className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-xs font-semibold text-white flex items-center gap-1.5 cursor-pointer transition-colors"
                    >
                      {ollamaBusy || ollamaStatus?.installing ? (
                        <>
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          <span>
                            {t('settings.costSaver.installingStep', {
                              step:
                                ollamaStatus?.step === 'installing'
                                  ? t('settings.costSaver.stepInstalling')
                                  : ollamaStatus?.step === 'starting'
                                  ? t('settings.costSaver.stepStarting')
                                  : t('settings.costSaver.stepPulling'),
                            })}
                          </span>
                        </>
                      ) : (
                        <>
                          <Download className="w-3.5 h-3.5" />
                          <span>
                            {ollamaStatus?.installed
                              ? ollamaStatus?.running
                                ? t('settings.costSaver.pullModelButton', {
                                    model: ollamaStatus.recommended_model || 'qwen2.5:7b',
                                  })
                                : t('settings.costSaver.startServerButton')
                              : t('settings.costSaver.installButton')}
                          </span>
                        </>
                      )}
                    </button>
                  )}
                </div>

                {ollamaStatus?.error && (
                  <p className="text-rose-400 text-[11px]">{ollamaStatus.error}</p>
                )}

                {/* Advanced Users manual link & CLI instructions */}
                <div className="bg-surface/40 border border-line/60 rounded-lg p-2.5 space-y-1.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-zinc-300 flex items-center gap-1.5">
                      <Terminal className="w-3.5 h-3.5 text-indigo-400" />
                      {t('settings.costSaver.advancedTitle')}
                    </span>
                    <a
                      href={ollamaStatus?.download_url || 'https://ollama.com/download'}
                      target="_blank"
                      rel="noreferrer"
                      data-testid="ollama-download-link"
                      className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300 font-medium"
                    >
                      <ExternalLink className="w-3 h-3" />
                      {t('settings.costSaver.downloadLink')}
                    </a>
                  </div>
                  <p className="text-zinc-400 leading-relaxed">
                    {t('settings.costSaver.advancedText')}
                  </p>
                  <code
                    dir="ltr"
                    className="block bg-sunken border border-line rounded px-2.5 py-1 text-[11px] font-mono text-emerald-300 select-all"
                  >
                    ollama pull qwen2.5:7b
                  </code>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-3 pt-1">
              {checkError && <span className="text-[11px] text-rose-400">{checkError}</span>}
              <button
                onClick={() => runCheck()}
                disabled={checking}
                title={t('settings.testHint')}
                className="me-auto px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-xs text-zinc-200 flex items-center gap-1.5 cursor-pointer"
              >
                {checking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlugZap className="w-3.5 h-3.5" />}
                {checking ? t('settings.testing') : t('settings.testModels')}
              </button>
              {dirty && <span className="text-[11px] text-amber-300">{t('settings.unsaved')}</span>}
              {error && <span className="text-[11px] text-rose-400">{error}</span>}
              <button
                onClick={() => setDraft(settings)}
                disabled={!dirty || saving}
                className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-xs text-zinc-200 cursor-pointer"
              >
                {t('settings.reset')}
              </button>
              <button
                onClick={onSave}
                disabled={!dirty || saving}
                className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-semibold text-white flex items-center gap-1.5 cursor-pointer"
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : justSaved ? (
                  <Check className="w-3.5 h-3.5" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                {justSaved ? t('settings.saved') : t('settings.save')}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Cost estimate */}
      <section className={card}>
        <div className="border-b border-line pb-3">
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <Coins className="w-4 h-4 text-amber-400" />
            {t('settings.estimate')}
          </h2>
          <p className="text-[11px] text-zinc-400 mt-1">{t('settings.estimateHint')}</p>
        </div>

        <div className="flex flex-wrap items-end gap-4 text-xs">
          <label className="space-y-1">
            <span className="block text-zinc-400 font-medium">{t('settings.episodeLength')}</span>
            <input
              type="number"
              min={1}
              max={600}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value) || 1)}
              className="w-24 bg-sunken border border-line rounded-lg px-3 py-1.5 text-zinc-200 focus:outline-none focus:border-indigo-500"
            />
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-zinc-300">
            <input type="checkbox" checked={withTranslation} onChange={(e) => setWithTranslation(e.target.checked)} />
            {t('settings.includeTranslation')}
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-zinc-300">
            <input type="checkbox" checked={withOnscreen} onChange={(e) => setWithOnscreen(e.target.checked)} />
            {t('settings.includeOnscreen')}
          </label>
        </div>

        {estimate && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="estimate-table">
              <thead className="text-[10px] uppercase tracking-wider text-zinc-500 border-b border-line">
                <tr>
                  <th className="py-2 text-start font-semibold">{t('settings.colStep')}</th>
                  <th className="py-2 text-start font-semibold">{t('settings.colModel')}</th>
                  <th className="py-2 text-end font-semibold">{t('settings.colRequests')}</th>
                  <th className="py-2 text-end font-semibold">{t('settings.colInput')}</th>
                  <th className="py-2 text-end font-semibold">{t('settings.colOutput')}</th>
                  <th className="py-2 text-end font-semibold">{t('settings.colCost')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {Object.entries(estimate.stages).map(([stage, s]) => (
                  <tr key={stage} className="text-zinc-300">
                    <td className="py-2">{t(`stages.${stage}` as MessageKey)}</td>
                    <td className="py-2 text-zinc-400">
                      {modelLabel(catalog, s!.model)}
                      <span className="text-zinc-500">
                        {' · '}
                        {thinkingLabel(t, s!.thinking, catalog.find((m) => m.id === s!.model))}
                      </span>
                      {s!.flex && <span className="text-emerald-400">{' · '}{t('settings.flexTag')}</span>}
                    </td>
                    <td className="py-2 text-end font-mono">{formatNumber(s!.requests, language)}</td>
                    <td className="py-2 text-end font-mono">{formatNumber(s!.input_tokens, language)}</td>
                    <td className="py-2 text-end font-mono">{formatNumber(s!.output_tokens, language)}</td>
                    <td className="py-2 text-end font-mono" dir="ltr">
                      {formatUsd(s!.cost_usd, language)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-line text-zinc-50 font-semibold">
                  <td className="py-2" colSpan={5}>
                    {t('settings.total')}
                  </td>
                  <td className="py-2 text-end font-mono" dir="ltr" data-testid="estimate-total">
                    ≈ {formatUsd(estimate.total_cost_usd, language)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
        <p className="text-[11px] text-zinc-500">{t('settings.estimateFootnote')}</p>
        {pricingSource && (
          <a
            href={pricingSource}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-indigo-400 hover:text-indigo-300"
          >
            <ExternalLink className="w-3 h-3" />
            {t('settings.source', { date: pricingChecked })}
          </a>
        )}
      </section>

      <StorageSection />

      {/* Diagnostics */}
      <section className={card}>
        <div className="border-b border-line pb-3">
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <Stethoscope className="w-4 h-4 text-indigo-400" />
            {t('settings.diagnostics')}
          </h2>
          <p className="text-[11px] text-zinc-400 mt-1">{t('settings.diagnosticsHint')}</p>
        </div>
        <a
          href={api.getServerLogUrl()}
          download
          data-testid="server-log"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200"
        >
          <ScrollText className="w-3.5 h-3.5" />
          {t('settings.serverLog')}
        </a>
      </section>
    </div>
  );
};

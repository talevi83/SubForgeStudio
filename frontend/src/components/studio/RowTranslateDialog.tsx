import React, { useMemo, useState } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { modelLabel, thinkingLabel } from '../settings/labels';
import { Loader2, Video } from 'lucide-react';
import type { Gender, SpeakerGuess, ThinkingLevel } from '../../types';
import { useT } from '../../i18n';

interface Props {
  count: number;
  onCancel: () => void;
  /** Ask the model to infer the genders from video frames; omitted when detection is unavailable */
  onDetect?: (model: string, thinking: ThinkingLevel) => Promise<SpeakerGuess>;
  /** Model the project last used; the picker starts here */
  defaultModel?: string;
  onSubmit: (opts: {
    speaker: Gender | null;
    addressee: Gender | null;
    hint: string;
    remember: boolean;
    model: string;
    thinking: ThinkingLevel;
  }) => void;
}

const selectClass =
  'w-full bg-sunken border border-line rounded-lg px-2 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500';

/** Ask who is speaking and who is being spoken to, before re-translating the selected rows. */
export const RowTranslateDialog: React.FC<Props> = ({ count, onCancel, onDetect, defaultModel, onSubmit }) => {
  const t = useT();
  const catalog = useSettingsStore((s) => s.catalog);
  const appSettings = useSettingsStore((s) => s.settings);
  const startModel = defaultModel || appSettings?.translation.model || '';
  const startThinking: ThinkingLevel = appSettings?.translation.thinking ?? 'default';
  const [model, setModel] = useState(startModel);
  const [thinking, setThinking] = useState<ThinkingLevel>(startThinking);
  const modelOptions = useMemo(() => {
    const ids = catalog.filter((m) => !m.tasks || m.tasks.includes('translation')).map((m) => m.id);
    return model && !ids.includes(model) ? [model, ...ids] : ids;
  }, [catalog, model]);
  const modelInfo = catalog.find((m) => m.id === model);
  const levels: ThinkingLevel[] = modelInfo?.thinking?.length ? modelInfo.thinking : ['default'];
  const pickModel = (id: string) => {
    setModel(id);
    const next = catalog.find((m) => m.id === id);
    if (next && !next.thinking.includes(thinking)) setThinking('default');
  };
  const changedAi = model !== startModel || thinking !== startThinking;
  const [speaker, setSpeaker] = useState<Gender | ''>('');
  const [addressee, setAddressee] = useState<Gender | ''>('');
  const [hint, setHint] = useState('');
  const [remember, setRemember] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [detectNote, setDetectNote] = useState<string | null>(null);

  const detect = async () => {
    if (!onDetect) return;
    setDetecting(true);
    setDetectNote(null);
    try {
      const g = await onDetect(model, thinking);
      if (g.speaker_gender) setSpeaker(g.speaker_gender);
      if (g.addressee_gender) setAddressee(g.addressee_gender);
      setDetectNote(
        !g.speaker_gender && !g.addressee_gender ? t('table.detectUnsure') : g.explanation || t('table.detectDone')
      );
    } catch (e) {
      setDetectNote(e instanceof Error ? e.message : String(e));
    } finally {
      setDetecting(false);
    }
  };

  const options = (
    <>
      <option value="">{t('table.genderAuto')}</option>
      <option value="male">{t('table.genderMale')}</option>
      <option value="female">{t('table.genderFemale')}</option>
      <option value="group">{t('table.genderGroup')}</option>
    </>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md bg-surface border border-line rounded-xl p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold text-zinc-100">{t('table.rowsDialogTitle', { count })}</h2>
        <p className="text-xs text-zinc-400 leading-relaxed">{t('table.rowsDialogHelp')}</p>

        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-[11px] text-zinc-400">
            <span>{t('table.speaker')}</span>
            <select value={speaker} onChange={(e) => setSpeaker(e.target.value as Gender | '')} className={selectClass}>
              {options}
            </select>
          </label>
          <label className="space-y-1 text-[11px] text-zinc-400">
            <span>{t('table.addressee')}</span>
            <select
              value={addressee}
              onChange={(e) => setAddressee(e.target.value as Gender | '')}
              className={selectClass}
              data-testid="addressee-select"
            >
              {options}
            </select>
          </label>
        </div>

        {modelOptions.length > 0 && (
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-[11px] text-zinc-400">
              <span>{t('table.model')}</span>
              <select
                value={model}
                onChange={(e) => pickModel(e.target.value)}
                className={selectClass}
                data-testid="row-model"
              >
                {modelOptions.map((id) => (
                  <option key={id} value={id}>{modelLabel(catalog, id)}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-[11px] text-zinc-400">
              <span>{t('table.thinking')}</span>
              <select
                value={thinking}
                onChange={(e) => setThinking(e.target.value as ThinkingLevel)}
                className={selectClass}
                data-testid="row-thinking"
              >
                {levels.map((lv) => (
                  <option key={lv} value={lv}>{thinkingLabel(t, lv, modelInfo)}</option>
                ))}
              </select>
            </label>
          </div>
        )}

        {onDetect && (
          <div className="space-y-1">
            <button
              onClick={() => void detect()}
              disabled={detecting}
              title={t('table.detectTitle')}
              data-testid="detect-speakers"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-line text-xs text-zinc-300 hover:text-white hover:border-indigo-500/60 disabled:opacity-60 disabled:cursor-wait"
            >
              {detecting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Video className="w-3 h-3" />}
              {detecting ? t('table.detecting') : t('table.detect')}
            </button>
            {detectNote && <p className="text-[11px] text-zinc-400" data-testid="detect-note">{detectNote}</p>}
          </div>
        )}

        <label className="block space-y-1 text-[11px] text-zinc-400">
          <span>{t('table.hintLabel')}</span>
          <input
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            placeholder={t('table.hintPlaceholder')}
            className={selectClass}
          />
        </label>

        <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
          />
          {t('table.rememberHint')}
        </label>

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-xs text-zinc-300 border border-line hover:text-white"
          >
            {t('table.cancel')}
          </button>
          <button
            onClick={() =>
              onSubmit({ speaker: speaker || null, addressee: addressee || null, hint: hint.trim(), remember, model, thinking })
            }
            disabled={!speaker && !addressee && !hint.trim() && !changedAi}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white"
            data-testid="rows-translate-submit"
          >
            {t('table.translateRows')}
          </button>
        </div>
      </div>
    </div>
  );
};

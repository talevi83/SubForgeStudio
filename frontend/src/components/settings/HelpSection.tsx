import React, { useState } from 'react';
import { useUiStore } from '../../store/useUiStore';
import { useT } from '../../i18n';
import type { MessageKey } from '../../i18n';
import { ArrowLeft, HelpCircle, ChevronDown, BookOpen } from 'lucide-react';

const card = 'bg-surface border border-line rounded-xl p-5 shadow-lg space-y-4';

const FAQ_KEYS: { q: MessageKey; a: MessageKey }[] = [
  { q: 'settings.help.q1', a: 'settings.help.a1' },
  { q: 'settings.help.q2', a: 'settings.help.a2' },
  { q: 'settings.help.q3', a: 'settings.help.a3' },
  { q: 'settings.help.q4', a: 'settings.help.a4' },
  { q: 'settings.help.q5', a: 'settings.help.a5' },
  { q: 'settings.help.q6', a: 'settings.help.a6' },
];

const WORKFLOW_STEPS: { title: MessageKey; text: MessageKey }[] = [
  { title: 'settings.help.step1Title', text: 'settings.help.step1Text' },
  { title: 'settings.help.step2Title', text: 'settings.help.step2Text' },
  { title: 'settings.help.step3Title', text: 'settings.help.step3Text' },
  { title: 'settings.help.step4Title', text: 'settings.help.step4Text' },
];

export const HelpSection: React.FC = () => {
  const t = useT();
  const setView = useUiStore((s) => s.setView);
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-10" data-testid="help-qa-screen">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-50">{t('settings.help.title')}</h1>
        <button
          onClick={() => setView('studio')}
          className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200 cursor-pointer"
        >
          <ArrowLeft className="w-3.5 h-3.5 rtl:rotate-180" />
          {t('settings.back')}
        </button>
      </div>

      {/* 4-Step Quick-Start Workflow */}
      <section className={card}>
        <div className="border-b border-line pb-3">
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-emerald-400" />
            {t('settings.help.workflowTitle')}
          </h2>
          <p className="text-[11px] text-zinc-400 mt-1">{t('settings.help.subtitle')}</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {WORKFLOW_STEPS.map((step, i) => (
            <div key={i} className="p-3.5 rounded-lg bg-sunken border border-line space-y-1">
              <div className="text-xs font-semibold text-indigo-300">{t(step.title)}</div>
              <p className="text-[11px] text-zinc-400 leading-relaxed">{t(step.text)}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Expandable Q&A Accordion */}
      <section className={card} data-testid="help-qa-section">
        <div className="border-b border-line pb-3">
          <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <HelpCircle className="w-4 h-4 text-indigo-400" />
            {t('settings.help.faqTitle')}
          </h2>
        </div>
        <div className="space-y-2.5">
          {FAQ_KEYS.map((item, idx) => {
            const isOpen = openIndex === idx;
            return (
              <div key={idx} className="rounded-lg bg-sunken border border-line overflow-hidden">
                <button
                  type="button"
                  onClick={() => setOpenIndex(isOpen ? null : idx)}
                  className="w-full px-3.5 py-2.5 text-start text-xs font-medium text-zinc-100 hover:bg-zinc-800/50 flex items-center justify-between gap-2 cursor-pointer"
                >
                  <span>{t(item.q)}</span>
                  <ChevronDown
                    className={`w-4 h-4 text-zinc-400 flex-shrink-0 transition-transform ${
                      isOpen ? 'rotate-180 text-indigo-400' : ''
                    }`}
                  />
                </button>
                {isOpen && (
                  <div className="px-3.5 pb-3 pt-1 text-[11px] text-zinc-300 leading-relaxed border-t border-line/60">
                    {t(item.a)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
};

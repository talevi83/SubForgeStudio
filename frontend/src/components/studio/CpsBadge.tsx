import React from 'react';
import { useT } from '../../i18n';

interface CpsBadgeProps {
  cps?: number;
  max?: number;
}

export const CpsBadge: React.FC<CpsBadgeProps> = ({ cps = 0, max = 20 }) => {
  const t = useT();
  const rounded = Number((cps || 0).toFixed(1));

  let colorClasses = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
  let label = t('cps.good');

  if (rounded > max) {
    colorClasses = 'bg-rose-500/15 text-rose-400 border-rose-500/30';
    label = t('cps.fast');
  } else if (rounded > max - 2) {
    colorClasses = 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    label = t('cps.brisk');
  }

  return (
    <div
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono border font-medium ${colorClasses}`}
      title={t('cps.title', { cps: rounded, label })}
      dir="ltr"
    >
      <span>{rounded}</span>
      <span className="text-[9px] opacity-75">CPS</span>
    </div>
  );
};

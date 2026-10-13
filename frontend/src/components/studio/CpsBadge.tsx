import React from 'react';

interface CpsBadgeProps {
  cps?: number;
}

export const CpsBadge: React.FC<CpsBadgeProps> = ({ cps = 0 }) => {
  const rounded = Number(cps.toFixed(1));

  let colorClasses = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
  let label = 'Good';

  if (rounded > 20) {
    colorClasses = 'bg-rose-500/15 text-rose-400 border-rose-500/30';
    label = 'Fast!';
  } else if (rounded > 18) {
    colorClasses = 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    label = 'Brisk';
  }

  return (
    <div
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono border font-medium ${colorClasses}`}
      title={`${rounded} Characters Per Second (${label})`}
    >
      <span>{rounded}</span>
      <span className="text-[9px] opacity-75">CPS</span>
    </div>
  );
};

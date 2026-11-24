// Shared UI primitives (glass panel, ring gauge, sparkline, badges). Import, don't fork.
import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';

export function Panel({ title, subtitle, icon, actions, onOpen, className = '', children, bodyClass = '' }: {
  title?: ReactNode; subtitle?: ReactNode; icon?: ReactNode; actions?: ReactNode; onOpen?: () => void;
  className?: string; bodyClass?: string; children?: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          {icon && <span className="panel-icon">{icon}</span>}
          <div className="panel-titles">
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          <div className="panel-actions">
            {actions}
            {onOpen && <button className="icon-btn" onClick={onOpen} aria-label="Ouvrir"><ChevronRight size={18} /></button>}
          </div>
        </header>
      )}
      <div className={`panel-body ${bodyClass}`}>{children}</div>
    </section>
  );
}

const GAUGE_COLORS: Record<string, [string, string]> = {
  cyan: ['#22c8ff', '#2f7bff'], violet: ['#8a5cff', '#2f7bff'], green: ['#2ee6a8', '#22c8ff'],
  amber: ['#ffb547', '#ff6b7a'], red: ['#ff6b7a', '#ff3d6e'],
};

/** Circular gauge. `value` null → shows "—" (never an invented number). */
export function RingGauge({ value, label, color = 'cyan', size = 92, stroke = 7, suffix = '%' }: {
  value: number | null | undefined; label: string; color?: keyof typeof GAUGE_COLORS; size?: number; stroke?: number; suffix?: string;
}) {
  const r = (size - stroke) / 2 - 2;
  const c = 2 * Math.PI * r;
  const v = value === null || value === undefined || Number.isNaN(value) ? null : Math.max(0, Math.min(100, value));
  const [a, b] = GAUGE_COLORS[color] ?? GAUGE_COLORS.cyan;
  const gid = `g-${label.replace(/\W/g, '')}-${color}`;
  return (
    <div className="ring-gauge" style={{ width: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label} ${v === null ? 'indisponible' : `${v.toFixed(0)}${suffix}`}`}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor={a} /><stop offset="100%" stopColor={b} /></linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(120,170,255,.12)" strokeWidth={stroke} />
        {v !== null && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#${gid})`} strokeWidth={stroke} strokeLinecap="round"
            strokeDasharray={`${(v / 100) * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
            style={{ filter: `drop-shadow(0 0 6px ${a}88)`, transition: 'stroke-dasharray .6s ease' }} />
        )}
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="ring-value" style={{ fill: a }}>
          {v === null ? '—' : `${v.toFixed(0)}${suffix}`}
        </text>
      </svg>
      <span className="ring-label">{label}</span>
    </div>
  );
}

export function Sparkline({ values, width = 120, height = 32, color = '#22c8ff', fill = true }: {
  values: number[]; width?: number; height?: number; color?: string; fill?: boolean;
}) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * width, height - 2 - ((v - min) / span) * (height - 4)]);
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="sparkline">
      {fill && <path d={`${d} L${width},${height} L0,${height} Z`} fill={color} opacity={0.12} />}
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" style={{ filter: `drop-shadow(0 0 3px ${color}aa)` }} />
    </svg>
  );
}

export function Badge({ children, tone = 'default', title }: { children: ReactNode; tone?: 'default' | 'cyan' | 'green' | 'amber' | 'red' | 'violet'; title?: string }) {
  return <span className={`badge badge-${tone}`} title={title}>{children}</span>;
}

export function StatusDot({ ok, warn = false, label }: { ok: boolean; warn?: boolean; label?: string }) {
  return <span className={`status-dot ${ok ? (warn ? 'warn' : 'ok') : 'off'}`} role="img" aria-label={label ?? (ok ? 'actif' : 'inactif')} />;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

/** « indisponible(s) » agrees in number with the first word of `what` (Données → pluriel, Historique → singulier). */
export function Unavailable({ what = 'Données', reason, plural }: { what?: string; reason?: string; plural?: boolean }) {
  const many = plural ?? /[sx]$/i.test(what.trim().split(/\s+/)[0] ?? '');
  return <div className="unavailable"><strong>{what} indisponible{many ? 's' : ''}</strong>{reason && <span>{reason}</span>}</div>;
}

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="Chargement" />;
}

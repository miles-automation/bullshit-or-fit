// ---------------------------------------------------------------------------
// charts.tsx — dependency-free SVG charts for the jobtrends dashboard.
// Responsive via viewBox; colors come from the shared design tokens.
// ---------------------------------------------------------------------------

const W = 760;
const H = 300;
const PAD = { top: 16, right: 16, bottom: 28, left: 40 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

export const SERIES_COLORS = [
  "#f05a2a",
  "#245cb8",
  "#0c8f6f",
  "#8b3fb0",
  "#c99700",
  "#0d8ea6",
  "#c03e18",
  "#4a5a86",
];

const xFor = (i: number, n: number) => PAD.left + (n <= 1 ? 0 : (i / (n - 1)) * PLOT_W);
const yFor = (v: number, min: number, max: number) =>
  PAD.top + (max === min ? PLOT_H : (1 - (v - min) / (max - min)) * PLOT_H);

function monthTicks(months: string[]): number[] {
  const n = months.length;
  if (n <= 8) return months.map((_, i) => i);
  const step = Math.ceil(n / 8);
  const idx = months.map((_, i) => i).filter((i) => i % step === 0);
  if (idx[idx.length - 1] !== n - 1) idx.push(n - 1);
  return idx;
}

function AxisFrame({
  months,
  min,
  max,
  fmtY,
}: {
  months: string[];
  min: number;
  max: number;
  fmtY: (v: number) => string;
}) {
  const gridlines = [0, 0.25, 0.5, 0.75, 1].map((t) => min + t * (max - min));
  return (
    <g>
      {gridlines.map((v, i) => (
        <g key={i}>
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={yFor(v, min, max)}
            y2={yFor(v, min, max)}
            className="chart-grid"
          />
          <text x={PAD.left - 6} y={yFor(v, min, max) + 3} className="chart-ylabel">
            {fmtY(v)}
          </text>
        </g>
      ))}
      {monthTicks(months).map((i) => (
        <text key={i} x={xFor(i, months.length)} y={H - 8} className="chart-xlabel">
          {months[i].slice(2)}
        </text>
      ))}
    </g>
  );
}

export interface Line {
  label: string;
  color: string;
  values: (number | null)[];
}

export function LineChart({
  months,
  lines,
  fmtY = (v) => `${Math.round(v)}%`,
}: {
  months: string[];
  lines: Line[];
  fmtY?: (v: number) => string;
}) {
  const all = lines.flatMap((l) => l.values).filter((v): v is number => v != null);
  if (!months.length || !all.length) return <div className="chart-empty">No data yet.</div>;
  const max = Math.max(...all) * 1.1 || 1;
  const min = 0;

  const path = (values: (number | null)[]) => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v == null) {
        pen = false;
        return;
      }
      const cmd = pen ? "L" : "M";
      d += `${cmd}${xFor(i, months.length).toFixed(1)} ${yFor(v, min, max).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      <AxisFrame months={months} min={min} max={max} fmtY={fmtY} />
      {lines.map((l) => (
        <g key={l.label}>
          <path d={path(l.values)} fill="none" stroke={l.color} strokeWidth={2.2} />
          {l.values.map((v, i) =>
            v == null ? null : (
              <circle
                key={i}
                cx={xFor(i, months.length)}
                cy={yFor(v, min, max)}
                r={2.4}
                fill={l.color}
              >
                <title>{`${l.label} · ${months[i]}: ${fmtY(v)}`}</title>
              </circle>
            ),
          )}
        </g>
      ))}
    </svg>
  );
}

/** Median line with a shaded p25–p75 band. */
export function BandChart({
  months,
  lo,
  mid,
  hi,
  fmtY,
}: {
  months: string[];
  lo: number[];
  mid: number[];
  hi: number[];
  fmtY: (v: number) => string;
}) {
  const all = [...lo, ...mid, ...hi].filter((v) => v > 0);
  if (!months.length || !all.length) return <div className="chart-empty">No data yet.</div>;
  const max = Math.max(...hi) * 1.1;
  const min = Math.min(...lo) * 0.9;

  const n = months.length;
  const band =
    hi.map((v, i) => `${xFor(i, n).toFixed(1)},${yFor(v, min, max).toFixed(1)}`).join(" ") +
    " " +
    lo
      .map((v, i) => `${xFor(n - 1 - i, n).toFixed(1)},${yFor(lo[n - 1 - i], min, max).toFixed(1)}`)
      .join(" ");
  const midLine = mid
    .map((v, i) => `${i === 0 ? "M" : "L"}${xFor(i, n).toFixed(1)} ${yFor(v, min, max).toFixed(1)}`)
    .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      <AxisFrame months={months} min={min} max={max} fmtY={fmtY} />
      <polygon points={band} fill="rgba(240,90,42,0.14)" stroke="none" />
      <path d={midLine} fill="none" stroke="#c03e18" strokeWidth={2.4} />
      {mid.map((v, i) => (
        <circle key={i} cx={xFor(i, n)} cy={yFor(v, min, max)} r={2.6} fill="#c03e18">
          <title>{`${months[i]}: median ${fmtY(v)} (p25 ${fmtY(lo[i])} – p75 ${fmtY(hi[i])})`}</title>
        </circle>
      ))}
    </svg>
  );
}

/** Stacked bars: returning (bottom) + new (top) = active; churned as a marker line. */
export function ChurnChart({
  months,
  returning,
  fresh,
  churned,
}: {
  months: string[];
  returning: number[];
  fresh: number[];
  churned: number[];
}) {
  if (!months.length) return <div className="chart-empty">No data yet.</div>;
  const active = returning.map((r, i) => r + fresh[i]);
  const max = Math.max(...active, ...churned) * 1.1;
  const min = 0;
  const n = months.length;
  const bw = Math.min(26, (PLOT_W / n) * 0.62);

  const churnLine = churned
    .map((v, i) => `${i === 0 ? "M" : "L"}${xFor(i, n).toFixed(1)} ${yFor(v, min, max).toFixed(1)}`)
    .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      <AxisFrame months={months} min={min} max={max} fmtY={(v) => `${Math.round(v)}`} />
      {months.map((m, i) => {
        const x = xFor(i, n) - bw / 2;
        const yRet = yFor(returning[i], min, max);
        const yTop = yFor(active[i], min, max);
        const base = yFor(0, min, max);
        return (
          <g key={m}>
            <rect x={x} y={yRet} width={bw} height={base - yRet} fill="#245cb8" rx={1.5}>
              <title>{`${m}: ${returning[i]} returning`}</title>
            </rect>
            <rect x={x} y={yTop} width={bw} height={yRet - yTop} fill="#0c8f6f" rx={1.5}>
              <title>{`${m}: ${fresh[i]} new`}</title>
            </rect>
          </g>
        );
      })}
      <path d={churnLine} fill="none" stroke="#c03e18" strokeWidth={2} strokeDasharray="4 3" />
      {churned.map((v, i) => (
        <circle key={i} cx={xFor(i, n)} cy={yFor(v, min, max)} r={2.4} fill="#c03e18">
          <title>{`${months[i]}: ${v} churned from prior month`}</title>
        </circle>
      ))}
    </svg>
  );
}

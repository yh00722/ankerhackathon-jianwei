/**
 * @name 趋势小图
 * @description 不依赖图表库的 SVG 柱状图 / 折线图。内部按 360 宽画（手机上 1:1，字够大），在宽屏上等比放大。
 * - 示例数据：浅色斜纹（柱）/ 空心点（线），图下注明「示例数据」
 * - 没有观察的那天：灰色短横，不当成 0
 */

export interface Pt {
  x: string; // 横轴标签，例如 10-16
  y: number | null;
  sample?: boolean;
}

const W = 360;

function xLabel(x: string): string {
  const d = x.split('-')[1];
  return d ? `${Number(d)}日` : x;
}

function summary(title: string, data: Pt[], unit: string): string {
  const last = [...data].reverse().find((p) => p.y != null);
  return `${title}：${data.length} 个点${last ? `，最近 ${last.y}${unit}` : ''}`;
}

export function BarChart({ data, title, unit = '', color = '#2c6a96', height = 150 }: {
  data: Pt[]; title: string; unit?: string; color?: string; height?: number;
}) {
  const top = 22;
  const bottom = 24;
  const h = height - top - bottom;
  const max = Math.max(1, ...data.map((p) => p.y ?? 0));
  const slot = W / Math.max(1, data.length);
  const bw = Math.max(6, Math.min(28, slot * 0.62));
  const every = data.length > 10 ? 2 : 1;
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={summary(title, data, unit)}>
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="#dfe5ec" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#b9c3cf" strokeWidth="3" />
          </pattern>
        </defs>
        <line x1="0" y1={top + h} x2={W} y2={top + h} stroke="#c9ced6" />
        {data.map((p, i) => {
          const cx = slot * i + slot / 2;
          if (p.y == null) {
            return <line key={i} x1={cx - bw / 3} x2={cx + bw / 3} y1={top + h - 2} y2={top + h - 2} stroke="#b9c3cf" strokeWidth="2" />;
          }
          const bh = Math.max(2, (h * p.y) / max);
          const last = i === data.length - 1;
          return (
            <g key={i}>
              <rect x={cx - bw / 2} y={top + h - bh} width={bw} height={bh} rx="3" fill={p.sample ? 'url(#hatch)' : color} />
              {(last || data.length <= 8 || i % every === 0) && (
                <text x={cx} y={top + h - bh - 5} fontSize="12" textAnchor="middle" fill="#1f2933" fontWeight={last ? 700 : 400}>
                  {p.y}
                </text>
              )}
              {i % every === 0 || last ? (
                <text x={cx} y={height - 6} fontSize="11" textAnchor="middle" fill="#4b5563">{xLabel(p.x)}</text>
              ) : null}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export function LineChart({ data, title, unit = '', color = '#1e6b45', height = 150, digits = 0 }: {
  data: Pt[]; title: string; unit?: string; color?: string; height?: number; digits?: number;
}) {
  const pts = data.filter((p) => p.y != null) as (Pt & { y: number })[];
  if (pts.length === 0) {
    return (
      <figure className="chart">
        <figcaption>{title}</figcaption>
        <div className="chart__empty">还没有记录</div>
      </figure>
    );
  }
  const top = 24;
  const bottom = 26;
  const left = 10;
  const right = 10;
  const h = height - top - bottom;
  const lo = Math.min(...pts.map((p) => p.y));
  const hi = Math.max(...pts.map((p) => p.y));
  const pad = (hi - lo) * 0.2 || Math.max(1, hi * 0.2);
  const y0 = Math.max(0, lo - pad);
  const y1 = hi + pad;
  const sx = (i: number) => left + ((W - left - right) * i) / Math.max(1, pts.length - 1);
  const sy = (v: number) => top + h - (h * (v - y0)) / (y1 - y0);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');
  const every = pts.length > 8 ? 2 : 1;
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={summary(title, pts, unit)}>
        <line x1="0" y1={top + h} x2={W} y2={top + h} stroke="#c9ced6" />
        <path d={path} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" />
        {pts.map((p, i) => {
          const last = i === pts.length - 1;
          return (
            <g key={i}>
              <circle cx={sx(i)} cy={sy(p.y)} r={last ? 5 : 4} fill={p.sample ? '#fff' : color} stroke={color} strokeWidth="2" />
              {(i === 0 || last || pts.length <= 6) && (
                <text x={sx(i)} y={sy(p.y) - 9} fontSize="12" textAnchor={i === 0 ? 'start' : last ? 'end' : 'middle'}
                  fill="#1f2933" fontWeight={last ? 700 : 400}>
                  {p.y.toFixed(digits)}{unit}
                </text>
              )}
              {(i % every === 0 || last) && (
                <text x={sx(i)} y={height - 7} fontSize="11" textAnchor={i === 0 ? 'start' : last ? 'end' : 'middle'} fill="#4b5563">
                  {xLabel(p.x)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

/** 图下的说明：示例数据、只比同条件 */
export function ChartNote({ sample, sameCondition }: { sample?: boolean; sameCondition?: boolean }) {
  if (!sample && !sameCondition) return null;
  return (
    <div className="chart__note">
      {sample ? '斜纹 / 空心点是示例数据，不是真实记录。' : ''}
      {sameCondition ? '只画同样条件（同一把椅子、同样规则）的记录。' : ''}
    </div>
  );
}

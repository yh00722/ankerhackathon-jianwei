/**
 * @name 康复师端
 * @description 一页趋势：近 14 天的日常活动与观测覆盖、每个训练动作的历史（分左右腿），以及训练计划。
 * 老人授权「分享给康复师」后才有内容（服务端 /api/therapist/summary 校验，未授权 403）。
 * 只把条件相同的记录放在一起；示例数据单独标出；不判断恢复好坏。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, EX_NAME, fmtDateTime, fmtDur, fmtTime, PlanItem, planLabel, VARIANT_TEXT } from '../api';
import { BarChart, ChartNote, LineChart } from '../components/Charts';
import { RoleBar } from '../components/RoleBar';

interface Rec {
  id: number;
  at: number;
  reps: number;
  valid: boolean;
  invalid_reasons: string[];
  is_sample: boolean;
  conditions: Record<string, any>;
  condition_key: string;
  details: Record<string, any>;
  line: string;
}

interface Summary {
  daily: {
    day: string; on_s: number; observable_s: number; coverage: number | null; rises_complete: number;
    rises_incomplete: number; rise_median_s: number | null; longest_sit_s: number | null; trainings: number; reminders: number;
  }[];
  exercises: Record<string, Rec[]>;
  plan: PlanItem[];
  notes: string[];
  disclaimer: string;
  ai?: { text: string; source: string; facts: string[] };
  trends?: {
    days: string[];
    has_sample: boolean;
    daily: {
      day: string; rises_complete: number; rise_median_s: number | null; observable_min: number; longest_sit_min: number | null;
      reminders: number; trainings: number; observed: boolean; sample: boolean;
    }[];
    exercises: Record<string, { day: string; reps: number; sample: boolean; hold_mean_s: number | null; best_s: number | null }[]>;
  };
}

function token(): string {
  const q = window.location.hash.split('?')[1] || '';
  return new URLSearchParams(q).get('token') || '';
}

/** 同条件的记录画成柱：高度 = 次数（站立平衡用最长保持秒数）。示例数据浅色，无效的画空心。 */
function Bars({ recs, kind }: { recs: Rec[]; kind: string }) {
  if (recs.length < 2) return null;
  const val = (r: Rec) => (kind === 'balance' ? r.details?.best_s ?? 0 : r.reps);
  const max = Math.max(1, ...recs.map(val));
  const w = 28;
  const h = 70;
  const top = 16; // 柱顶数字留的位置
  return (
    <svg className="bars" width={recs.length * (w + 8)} height={top + h + 18} role="img" aria-label="同条件记录对比">
      {recs.map((r, i) => {
        const v = val(r);
        const bh = Math.max(2, (h * v) / max);
        const x = i * (w + 8);
        const y0 = top + h;
        return (
          <g key={r.id}>
            <rect x={x} y={y0 - bh} width={w} height={bh} rx={3}
              fill={!r.valid ? 'none' : r.is_sample ? '#cfd6df' : '#2f9e6b'} stroke={r.valid ? 'none' : '#9aa4b2'} />
            <text x={x + w / 2} y={y0 - bh - 4} fontSize={11} textAnchor="middle" fill="#334">{kind === 'balance' ? v.toFixed(0) : v}</text>
            <text x={x + w / 2} y={y0 + 14} fontSize={10} textAnchor="middle" fill="#778">
              {new Date(r.at * 1000).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric' })}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function condText(c: Record<string, any>): string {
  const parts: string[] = [];
  if (c.chair) parts.push(c.chair);
  if (c.armrest) parts.push(c.armrest);
  if (c.side) parts.push({ both: '两腿', left: '左腿', right: '右腿' }[c.side as string] || c.side);
  if (c.variant) parts.push(VARIANT_TEXT[c.variant] || c.variant);
  if (c.hold_s) parts.push(`保持 ${c.hold_s} 秒`);
  if (c.target_reps) parts.push(`目标 ${c.target_reps} 次`);
  if (c.target_s) parts.push(`目标 ${c.target_s} 秒`);
  return parts.join(' · ') || '默认条件';
}

function PlanEditor({ plan, onSaved }: { plan: PlanItem[]; onSaved: () => void }) {
  const [msg, setMsg] = useState('');
  const save = async (kind: string, body: any) => {
    try {
      await api(`/api/plan/${kind}`, { method: 'PUT', json: body });
      setMsg('已保存');
      onSaved();
    } catch (e: any) {
      setMsg(`保存失败：${e.message}`);
    }
  };
  return (
    <div>
      {plan.map((p) => (
        <div className="plan-row" key={p.kind}>
          <label>
            <input type="checkbox" checked={p.enabled} onChange={(e) => save(p.kind, { enabled: e.target.checked })} />
            <b>{EX_NAME[p.kind] || p.kind}</b>
          </label>
          {'target_reps' in p.params && (
            <label>次数
              <input type="number" min={1} max={50} defaultValue={p.params.target_reps}
                onBlur={(e) => save(p.kind, { params: { target_reps: e.target.value } })} />
            </label>
          )}
          {'hold_s' in p.params && (
            <label>每次保持（秒）
              <input type="number" min={1} max={30} step={0.5} defaultValue={p.params.hold_s}
                onBlur={(e) => save(p.kind, { params: { hold_s: e.target.value } })} />
            </label>
          )}
          {'target_s' in p.params && (
            <label>目标（秒）
              <input type="number" min={1} max={60} defaultValue={p.params.target_s}
                onBlur={(e) => save(p.kind, { params: { target_s: e.target.value } })} />
            </label>
          )}
          {'side' in p.params && (
            <label>腿
              <select defaultValue={p.params.side} onChange={(e) => save(p.kind, { params: { side: e.target.value } })}>
                <option value="both">两腿</option>
                <option value="left">左腿</option>
                <option value="right">右腿</option>
              </select>
            </label>
          )}
          {'variant' in p.params && (
            <label>站姿
              <select defaultValue={p.params.variant} onChange={(e) => save(p.kind, { params: { variant: e.target.value } })}>
                {Object.entries(VARIANT_TEXT).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
              </select>
            </label>
          )}
          <span className="muted small">{planLabel(p)}</span>
        </div>
      ))}
      {msg && <div className="muted small">{msg}</div>}
      <div className="muted small">系统不预设动作：每个动作要由康复师在这里打开，老人端才会出现。</div>
    </div>
  );
}

export function TherapistPage() {
  const [data, setData] = useState<Summary | null>(null);
  const [locked, setLocked] = useState<{ reason: string; revoked_at?: number } | null>(null);

  const load = useCallback(() => {
    api<Summary>(`/api/therapist/summary?token=${encodeURIComponent(token())}`)
      .then((d) => {
        setData(d);
        setLocked(null);
      })
      .catch((e) => {
        if (e.status === 403) {
          setData(null);
          setLocked(e.data?.detail || { reason: 'not_shared' });
        }
      });
  }, []);

  useEffect(() => {
    load();
    const iv = window.setInterval(load, 5000);
    return () => window.clearInterval(iv);
  }, [load]);

  return (
    <div className="therapist">
      <RoleBar current="therapist" />
      <div className="family__brand">见微知行 · 康复师端</div>
      {locked && (
        <div className="lock">
          <div className="lock__icon">🔒</div>
          <div className="lock__title">
            {locked.reason === 'revoked' ? '本人已停止分享' : locked.reason === 'not_paired' ? '这个链接无效' : '本人还没有分享给康复师'}
          </div>
          {locked.revoked_at && <div className="muted">{fmtTime(locked.revoked_at)} 停止</div>}
        </div>
      )}
      {data && (
        <>
          {data.ai && (
            <div className="card2 ai-card">
              <div className="ai-card__head">🤖 AI 总结（近两周）</div>
              <div className="ai-card__text">{data.ai.text}</div>
              <ul className="ai-card__facts">{data.ai.facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
              <div className="muted small">
                {data.ai.source === 'llm' ? '措辞由大模型改写，数字已和下面的记录逐一核对。' : '按记录自动整理。'}只陈述数字与同条件的前后对比，不判断恢复好坏，不替代临床评估。
              </div>
            </div>
          )}
          {data.trends && (
            <div className="card2">
              <div className="card2__title">趋势（近 14 天）</div>
              <div className="chart-grid">
                <BarChart title="每天完整看到站起来（次）" data={data.trends.daily.map((r) => ({ x: r.day, y: r.observed ? r.rises_complete : null, sample: r.sample }))} />
                <LineChart title="离座到站直用时，每天中位数（秒）" digits={1}
                  data={data.trends.daily.map((r) => ({ x: r.day, y: r.rise_median_s, sample: r.sample }))} />
                <BarChart title="每天最长连续坐着（分钟）" color="#8a6d3b"
                  data={data.trends.daily.map((r) => ({ x: r.day, y: r.observed ? r.longest_sit_min : null, sample: r.sample }))} />
                <BarChart title="每天看得清的时长（分钟）" color="#5b7083"
                  data={data.trends.daily.map((r) => ({ x: r.day, y: r.observed ? r.observable_min : null, sample: r.sample }))} />
                {(data.trends.exercises.sts30 || []).length > 0 && (
                  <LineChart title="30 秒坐站（次，同条件）" data={data.trends.exercises.sts30.map((r) => ({ x: r.day, y: r.reps, sample: r.sample }))} />
                )}
                {(data.trends.exercises.knee_ext || []).length > 0 && (
                  <LineChart title="坐位伸膝：平均每次保持（秒，同条件）" digits={1}
                    data={data.trends.exercises.knee_ext.map((r) => ({ x: r.day, y: r.hold_mean_s, sample: r.sample }))} />
                )}
                {(data.trends.exercises.balance || []).length > 0 && (
                  <LineChart title="站立平衡：最长保持（秒，同条件）" digits={1}
                    data={data.trends.exercises.balance.map((r) => ({ x: r.day, y: r.best_s, sample: r.sample }))} />
                )}
              </div>
              <ChartNote sample={data.trends.has_sample} sameCondition />
            </div>
          )}
          <div className="card2">
            <div className="card2__title">日常活动（近 14 天，按北京时间）</div>
            {data.daily.length === 0 ? (
              <div className="muted">这段时间没有开启日常观察。</div>
            ) : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>日期</th><th>开启</th><th>看得清</th><th>覆盖</th><th>完整起身</th><th>没看全</th>
                      <th>起身用时（中位）</th><th>最长连续坐</th><th>久坐提醒</th><th>训练</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.daily.map((d) => (
                      <tr key={d.day}>
                        <td>{d.day}</td>
                        <td>{fmtDur(d.on_s)}</td>
                        <td>{fmtDur(d.observable_s)}</td>
                        <td>{d.coverage != null ? `${Math.round(d.coverage * 100)}%` : '—'}</td>
                        <td>{d.rises_complete}</td>
                        <td>{d.rises_incomplete}</td>
                        <td>{d.rise_median_s != null ? `${d.rise_median_s.toFixed(1)} 秒` : '—'}</td>
                        <td>{d.longest_sit_s != null ? fmtDur(d.longest_sit_s) : '—'}</td>
                        <td>{d.reminders}</td>
                        <td>{d.trainings}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="muted small">「没看全」不计入次数；覆盖 = 看得清的时长 ÷ 开启时长。</div>
          </div>

          <div className="card2">
            <div className="card2__title">训练记录</div>
            {Object.keys(data.exercises).length === 0 && <div className="muted">还没有训练记录。</div>}
            {Object.entries(data.exercises).map(([kind, recs]) => {
              const groups: Record<string, Rec[]> = {};
              recs.forEach((r) => (groups[r.condition_key] ||= []).push(r));
              return (
                <div className="rec" key={kind}>
                  <div className="rec__head">{EX_NAME[kind] || kind}</div>
                  {Object.values(groups).map((g) => (
                    <div key={g[0].condition_key}>
                      <div className="muted small">条件：{condText(g[0].conditions)}（只和同条件的记录并列）</div>
                      <Bars recs={g} kind={kind} />
                      {g.slice().reverse().slice(0, 4).map((r) => (
                        <div key={r.id}>
                          {fmtDateTime(r.at)} {r.line}
                          {r.is_sample && <span className="tag">示例数据</span>}
                          {!r.valid && <span className="tag tag--warn">无效</span>}
                          {r.details?.angle_max_experimental && Object.values(r.details.angle_max_experimental).some((v) => v != null) && (
                            <span className="tag">伸直角度（实验性）：
                              {Object.entries(r.details.angle_max_experimental).filter(([, v]) => v != null)
                                .map(([s, v]) => `${s === 'left' ? '左' : '右'} ${v}°`).join('、')}
                            </span>
                          )}
                          {r.details?.flexion_max_experimental && Object.values(r.details.flexion_max_experimental).some((v) => v != null) && (
                            <span className="tag">屈曲角度（实验性）：
                              {Object.entries(r.details.flexion_max_experimental).filter(([, v]) => v != null)
                                .map(([s, v]) => `${s === 'left' ? '左' : '右'} ${v}°`).join('、')}
                            </span>
                          )}
                          {kind === 'balance' && r.details?.attempts && (
                            <span className="tag">
                              {r.details.attempts.map((a: any) => `${a.hold_s} 秒${a.support === 'yes' ? '（自报扶了）' : a.support === 'no' ? '（自报没扶）' : ''}`).join(' / ')}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>

          <div className="card2">
            <div className="card2__title">训练计划</div>
            <PlanEditor plan={data.plan} onSaved={load} />
          </div>

          <div className="card2">
            {data.notes.map((n, i) => <div className="muted small" key={i}>· {n}</div>)}
            <div className="disclaimer">{data.disclaimer}</div>
          </div>
        </>
      )}
    </div>
  );
}

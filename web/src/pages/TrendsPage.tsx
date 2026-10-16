/**
 * @name 两周趋势（老人自己看）
 * @description 独立页面：AI 小结 + 每天完整站起来、最长连续坐着、各训练动作（只画同样条件的记录）。
 * 入口：老人端「看两周趋势和 AI 小结」按钮（#/trends，页面自带返回）；
 *      安卓 App 底部「趋势」页签（#/trends?app=1，标题由 App 显示，网页不再重复）。
 * 没观察的那天画灰色短横，不当成 0；示例数据用斜纹 / 空心点标出。
 */

import { ReactElement, useEffect, useState } from 'react';
import { api } from '../api';
import { BarChart, ChartNote, LineChart, Pt } from '../components/Charts';

const IN_APP_TAB = window.location.hash.includes('app=1');

interface Day {
  day: string;
  observed: boolean;
  sample: boolean;
  rises_complete: number;
  longest_sit_min: number | null;
}
interface ExRec {
  day: string;
  reps: number;
  sample: boolean;
  hold_mean_s: number | null;
  best_s: number | null;
}
interface Trends {
  daily: Day[];
  exercises: Record<string, ExRec[]>;
  has_sample: boolean;
  ai?: { text: string; source: string };
}

function back() {
  if (window.history.length > 1) window.history.back();
  else window.location.hash = '#/elder';
}

function series(recs: ExRec[] | undefined, pick: (r: ExRec) => number | null): Pt[] {
  return (recs || []).map((r) => ({ x: r.day, y: pick(r), sample: r.sample }));
}

export function TrendsPage() {
  const [t, setT] = useState<Trends | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    const load = () =>
      api<Trends>('/api/trends')
        .then((d) => {
          setT(d);
          setErr(false);
        })
        .catch(() => setErr(true));
    load();
    // AI 小结第一次在后台生成：先显示模板，过几秒再取一次就是大模型整理的版本
    const soon = window.setTimeout(load, 8000);
    const iv = window.setInterval(load, 60_000);
    return () => {
      window.clearTimeout(soon);
      window.clearInterval(iv);
    };
  }, []);

  const days = t?.daily || [];
  const ex = t?.exercises || {};
  const charts: ReactElement[] = [];
  if (t) {
    charts.push(
      <BarChart title="每天完整看到站起来几次" unit=" 次"
        data={days.map((r) => ({ x: r.day, y: r.observed ? r.rises_complete : null, sample: r.sample }))} />,
      <BarChart title="每天最长连续坐着（分钟）" unit=" 分钟" color="#8a4b00"
        data={days.map((r) => ({ x: r.day, y: r.observed ? r.longest_sit_min : null, sample: r.sample }))} />,
    );
    if ((ex.sts30 || []).length > 1) {
      charts.push(<LineChart title="30 秒坐站（次）" unit=" 次" data={series(ex.sts30, (r) => r.reps)} />);
    }
    if ((ex.knee_ext || []).length > 1) {
      charts.push(<LineChart title="坐位伸膝：平均每次保持（秒）" unit=" 秒" digits={1} data={series(ex.knee_ext, (r) => r.hold_mean_s)} />);
    }
    if ((ex.balance || []).length > 1) {
      charts.push(<LineChart title="站立平衡：最长保持（秒）" unit=" 秒" digits={1} data={series(ex.balance, (r) => r.best_s)} />);
    }
  }

  return (
    <div className="elder trends">
      {!IN_APP_TAB && (
        <header className="page-top">
          <button className="page-back" onClick={back}>‹ 返回</button>
          <h1>两周趋势</h1>
        </header>
      )}
      {!t && <div className="card2">{err ? '暂时连不上电脑上的分析服务，稍后会自动再试。' : '正在整理…'}</div>}
      {t && (
        <>
          <section className="ai-card trends__ai">
            <div className="ai-card__head">🤖 这两周的小结</div>
            <div className="ai-card__text">{t.ai?.text || '—'}</div>
            <div className="e-note">
              {t.ai?.source === 'llm' ? '由 AI 整理成口语，数字都和记录核对过。' : '按记录自动生成。'}
              {days.length > 0 && ` 统计范围：${days[0].day} 到 ${days[days.length - 1].day}。`}
            </div>
          </section>
          <div className="trends__grid">
            {charts.map((c, i) => (
              <section className="card2" key={i}>{c}</section>
            ))}
          </div>
          <ChartNote sample={t.has_sample} sameCondition />
          <div className="e-note">没观察的那天画成灰色短横，不算 0。这里只有次数和时长，不保存画面。</div>
        </>
      )}
    </div>
  );
}

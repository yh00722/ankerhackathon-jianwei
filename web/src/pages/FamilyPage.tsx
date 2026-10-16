/**
 * @name 家属端
 * @description 只读摘要：老人授权后才有内容；未分享、已撤销都显示明确的锁定状态，不是白屏。
 * 权限在服务端校验（/api/family/summary 未授权返回 403），这里只负责展示。
 */

import { useEffect, useState } from 'react';
import { api, fmtTime } from '../api';
import { BarChart, ChartNote, LineChart, SampleTag } from '../components/Charts';
import { StatValue } from '../components/StatValue';
import { RoleBar } from '../components/RoleBar';

interface Summary {
  trained_days: number;
  by_label: Record<string, { rises_complete: number; observable_s: number }>;
  lines: string[];
  disclaimer: string;
  sts: { last: { reps: number; is_sample: boolean }; previous_same_condition: { reps: number; is_sample: boolean } | null } | null;
  rewrite: { text: string; source: string } | null;
  ai?: { text: string; source: string; facts: string[]; sample?: boolean };
  trends?: {
    days: string[];
    has_sample: boolean;
    daily: { day: string; rises_complete: number; trainings: number; observed: boolean; sample: boolean }[];
    sts: { day: string; reps: number; sample: boolean }[];
  };
}

/** 从老人端「分享给家人 / 康复师」页点「看看对方看到什么」进来（back=1）：顶部显示返回 */
const BACK = window.location.hash.includes('back=1');

function token(): string {
  const q = window.location.hash.split('?')[1] || '';
  return new URLSearchParams(q).get('token') || '';
}

export function FamilyPage() {
  const [data, setData] = useState<Summary | null>(null);
  const [locked, setLocked] = useState<{ reason: string; revoked_at?: number } | null>(null);
  const [updated, setUpdated] = useState<number | null>(null);

  useEffect(() => {
    const load = () =>
      api<Summary>(`/api/family/summary?token=${encodeURIComponent(token())}`)
        .then((d) => {
          setData(d);
          setLocked(null);
          setUpdated(Date.now() / 1000);
        })
        .catch((e) => {
          if (e.status === 403) {
            setData(null);
            setLocked(e.data?.detail || { reason: 'not_shared' });
          }
        });
    load();
    const iv = window.setInterval(load, 3000);
    return () => window.clearInterval(iv);
  }, []);

  // 顶部三个数字和下面的图、AI 小结用同一份数据（近 7 天）；用到示例数据时标题旁标「示例」
  const last7 = data?.trends?.daily.slice(-7) || [];
  const week = data?.trends
    ? { trained: last7.filter((d) => d.trainings > 0).length,
        rises: last7.reduce((n, d) => n + (d.observed ? d.rises_complete : 0), 0),
        sample: last7.some((d) => d.sample) }
    : { trained: data?.trained_days ?? 0,
        rises: Object.values(data?.by_label || {}).reduce((n, x) => n + (x.rises_complete || 0), 0),
        sample: false };

  return (
    <div className="family">
      {BACK && <button className="page-back page-back--inline" onClick={() => window.history.back()}>‹ 返回分享设置</button>}
      <RoleBar current="family" />
      <div className="family__brand">见微知行（家属端）</div>
      {locked && (
        <div className="lock">
          <div className="lock__icon">🔒</div>
          {locked.reason === 'revoked' ? (
            <>
              <div className="lock__title">妈妈已停止分享</div>
              <div className="muted">{locked.revoked_at ? `${fmtTime(locked.revoked_at)} 停止，` : ''}之前的内容不再更新。</div>
            </>
          ) : locked.reason === 'not_paired' ? (
            <div className="lock__title">这个链接无效</div>
          ) : (
            <>
              <div className="lock__title">妈妈还没有分享记录</div>
              <div className="muted">分享与否由她决定。可以先和她聊聊。</div>
            </>
          )}
        </div>
      )}
      {data && (
        <div className="card2">
          <div className="card2__title">妈妈近 7 天的行动小结{week.sample && <SampleTag />}</div>
          <div className="muted small">妈妈授权分享，不含任何画面</div>
          <div className="e-stats fam-stats">
            <div><StatValue text={week.trained} /><span>天练过</span></div>
            <div><StatValue text={week.rises} /><span>次完整<wbr />站起来</span></div>
            <div>
              <StatValue text={data.sts?.last ? data.sts.last.reps : '—'} />
              <span>30 秒坐站{data.sts?.last?.is_sample && <SampleTag />}</span>
            </div>
          </div>
          {data.ai && (
            <div className="ai-card">
              <div className="ai-card__head">🤖 AI 小结{data.ai.sample && <SampleTag />}</div>
              <div className="ai-card__text">{data.ai.text}</div>
              {data.ai.source === 'llm' && <div className="ai-card__meta">AI 整理，数字已核对</div>}
            </div>
          )}
          {data.trends && (
            <>
              <BarChart title="最近两周：每天完整看到站起来几次"
                data={data.trends.daily.map((r) => ({ x: r.day, y: r.observed ? r.rises_complete : null, sample: r.sample }))} unit=" 次" />
              {data.trends.sts.length > 1 && (
                <LineChart title="30 秒坐站（同样条件）" data={data.trends.sts.map((r) => ({ x: r.day, y: r.reps, sample: r.sample }))} unit=" 次" />
              )}
              <ChartNote sample={data.trends.has_sample} />
            </>
          )}
          <details className="fam-more">
            <summary>逐条记录</summary>
            <div className="fam-line">本周按计划练了 {data.trained_days} 天。</div>
            {data.lines.map((l, i) => (
              <div className="fam-line" key={i}>{l}</div>
            ))}
            {data.lines.length === 0 && <div className="fam-line muted">这周还没有开启观察。</div>}
          </details>
          <div className="disclaimer">{data.disclaimer}</div>
          {updated && <div className="muted small">更新于 {fmtTime(updated)}</div>}
        </div>
      )}
    </div>
  );
}

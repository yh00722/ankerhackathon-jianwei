/**
 * @name 家属端
 * @description 只读摘要：老人授权后才有内容；未分享、已撤销都显示明确的锁定状态，不是白屏。
 * 权限在服务端校验（/api/family/summary 未授权返回 403），这里只负责展示。
 */

import { useEffect, useState } from 'react';
import { api, fmtTime } from '../api';

interface Summary {
  trained_days: number;
  lines: string[];
  disclaimer: string;
  sts: { last: { reps: number; is_sample: boolean }; previous_same_condition: { reps: number; is_sample: boolean } | null } | null;
  rewrite: { text: string; source: string } | null;
}

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

  return (
    <div className="family">
      <div className="family__brand">见微知行 · 家属端</div>
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
          <div className="card2__title">妈妈本周的行动小结</div>
          <div className="muted small">由妈妈授权分享 · 一句话 + 数字 · 不包含任何画面</div>
          {data.rewrite && (
            <div className="fam-line fam-line--lead">
              {data.rewrite.text}
              <div className="muted small">措辞由大模型改写，里面的数字已和下方记录逐一核对</div>
            </div>
          )}
          <div className="fam-line">本周按计划练了 {data.trained_days} 天。</div>
          {data.lines.map((l, i) => (
            <div className="fam-line" key={i}>{l}</div>
          ))}
          {data.lines.length === 0 && <div className="fam-line muted">这周还没有开启观察。</div>}
          <div className="disclaimer">{data.disclaimer}</div>
          {updated && <div className="muted small">更新于 {fmtTime(updated)}</div>}
        </div>
      )}
    </div>
  );
}

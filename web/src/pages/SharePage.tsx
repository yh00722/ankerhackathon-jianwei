/**
 * @name 分享给家人 / 康复师（老人自己设置）
 * @description 独立页面：家人、康复师分开授权；能预览对方看到的样子，随时停止；下面是最近谁看过。
 * 入口：老人端「👪 分享给家人 / 康复师」按钮（#/share，页面自带返回）；
 *      安卓 App 顶栏右上角「分享」（#/share?app=1，在原生页面里打开，返回用顶栏箭头）。
 * 画面永远不分享，只分享次数和时长。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, fmtTime } from '../api';

const IN_APP = window.location.hash.includes('app=1');

interface Share {
  level: string;
  therapist_level: string;
  family_token: string;
  therapist_token: string;
}
interface Access {
  who: string;
  at: number;
  allowed: number;
}

function back() {
  if (window.history.length > 1) window.history.back();
  else window.location.hash = '#/elder';
}

const WHO: Record<string, string> = { family: '家人', therapist: '康复师' };

export function SharePage() {
  const [s, setS] = useState<Share | null>(null);
  const [log, setLog] = useState<Access[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const load = useCallback(() => {
    api<Share>('/api/share').then(setS).catch(() => setMsg('暂时连不上电脑上的分析服务'));
    api<Access[]>('/api/access_log').then(setLog).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    // App 里切回「分享」页签时重新取一次（分享状态可能在别处改过）
    window.addEventListener('jw:show', load);
    return () => window.removeEventListener('jw:show', load);
  }, [load]);

  const set = async (json: Partial<Share>, done: string) => {
    setBusy(true);
    try {
      await api('/api/share', { method: 'PUT', json });
      setMsg(done);
      load();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
      window.setTimeout(() => setMsg(''), 4000);
    }
  };

  const famOn = s?.level === 'summary';
  const thOn = s?.therapist_level === 'full';
  // 预览：在这台手机上打开对方的页面（没授权时看到的就是锁定页）；back=1 让对方页面显示「返回」
  const preview = (who: 'family' | 'therapist') => {
    if (!s) return;
    window.location.hash = who === 'family'
      ? `#/family?token=${encodeURIComponent(s.family_token)}&back=1`
      : `#/therapist?token=${encodeURIComponent(s.therapist_token)}&back=1`;
  };

  return (
    <div className="elder share">
      {!IN_APP && (
        <header className="page-top">
          <button className="page-back" onClick={back}>‹ 返回</button>
          <h1>分享给家人 / 康复师</h1>
        </header>
      )}
      <p className="share__lead">🔒 画面永远不分享，只分享次数和时长。随时可以停止。</p>

      <section className="card2 share__card">
        <div className="share__head">
          <span>👨‍👩‍👧 家人（子女）</span>
          <span className={famOn ? 'share__st share__st--on' : 'share__st'}>{famOn ? '正在分享' : '没有分享'}</span>
        </div>
        <p>家人能看到：本周练了几天、每天完整站起来几次、看得清多久、30 秒坐站和上次比，以及两周的次数趋势和 AI 小结。</p>
        <p className="e-note">看不到：画面、离座用时、每次保持几秒这些细节。</p>
        {famOn ? (
          <button className="e-btn e-btn--ghost" disabled={busy || !s} onClick={() => set({ level: 'none' }, '已停止给家人看')}>停止给家人看</button>
        ) : (
          <button className="e-btn" disabled={busy || !s} onClick={() => set({ level: 'summary' }, '家人现在能看到了')}>开始给家人看</button>
        )}
        <button className="share__preview" disabled={!s} onClick={() => preview('family')}>👀 看看家人那边看到什么 ›</button>
      </section>

      <section className="card2 share__card">
        <div className="share__head">
          <span>🩺 康复师</span>
          <span className={thOn ? 'share__st share__st--on' : 'share__st'}>{thOn ? '正在分享' : '没有分享'}</span>
        </div>
        <p>康复师能看到：每天的活动、离座到站直的用时、每个训练动作的次数和保持秒数、两周趋势和 AI 总结。</p>
        <p className="e-note">看不到：画面。和给家人看是分开设置的。</p>
        {thOn ? (
          <button className="e-btn e-btn--ghost" disabled={busy || !s} onClick={() => set({ therapist_level: 'none' }, '已停止给康复师看')}>停止给康复师看</button>
        ) : (
          <button className="e-btn" disabled={busy || !s} onClick={() => set({ therapist_level: 'full' }, '康复师现在能看到了')}>开始给康复师看</button>
        )}
        <button className="share__preview" disabled={!s} onClick={() => preview('therapist')}>👀 看看康复师那边看到什么 ›</button>
      </section>

      <section className="card2">
        <div className="share__head"><span>最近谁看过</span></div>
        {log.length === 0 ? (
          <p className="e-note">还没有人看过。</p>
        ) : (
          log.slice(0, 6).map((x, i) => (
            <div className="share__log" key={i}>
              <span className="share__t">{fmtTime(x.at)}</span>
              <span>{x.allowed ? `${WHO[x.who] || '有人'}看了` : `${WHO[x.who] || '不认识的链接'}想看，没让看`}</span>
            </div>
          ))
        )}
      </section>

      {msg && <div className="toast" role="status">{msg}</div>}
    </div>
  );
}

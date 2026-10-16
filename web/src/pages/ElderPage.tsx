/**
 * @name 老人端
 * @description 嵌在安卓 App 的 WebView 里。大字、少按钮：开始 / 暂停 / 结束观察，训练与 30 秒坐站，
 * 今天的记录，分享设置。所有状态以设备确认为准：摄像头没确认转到位，就不显示「正在观察」。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, fmtDur, fmtTime, LiveEvent, REASON_TEXT, useLive } from '../api';

type Today = Awaited<ReturnType<typeof loadToday>>;

function loadToday() {
  return api<{
    sessions: any[];
    total: { on_s: number; observable_s: number; rises_complete: number; rises_incomplete: number };
    sts: { last: any; previous_same_condition: any } | null;
  }>('/api/elder/today');
}

export function ElderPage() {
  const [label, setLabel] = useState<'alone' | 'companion'>('alone');
  const [today, setToday] = useState<Today | null>(null);
  const [share, setShare] = useState<{ level: string } | null>(null);
  const [ask, setAsk] = useState<LiveEvent | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [toast, setToast] = useState('');
  const [log, setLog] = useState<{ who: string; at: number; allowed: number }[]>([]);

  const refresh = useCallback(() => {
    loadToday().then(setToday).catch(() => {});
    api('/api/share').then(setShare).catch(() => {});
    api('/api/access_log').then(setLog).catch(() => {});
  }, []);

  const { state, connected } = useLive((e) => {
    if (e.kind === 'rise' && e.other_person && e.id) setAsk(e);
    if (e.kind === 'rise' || e.kind === 'sts_result' || e.kind === 'session') refresh();
  });

  useEffect(() => {
    refresh();
    const iv = window.setInterval(refresh, 8000);
    return () => window.clearInterval(iv);
  }, [refresh]);

  const go = async (fn: () => Promise<any>) => {
    try {
      await fn();
      refresh();
    } catch (e: any) {
      setToast(e.message);
      window.setTimeout(() => setToast(''), 4000);
    }
  };

  const sess = state?.session;
  const active = sess && sess.status !== 'ended';
  const v = state?.view;
  const sts = state?.sts;
  const stsRunning = sts && !['done', 'aborted'].includes(sts.phase);

  let title = '观察已关闭';
  let sub = '摄像头现在不为本应用工作。';
  let tone = 'idle';
  if (!connected) {
    title = '连接中…';
    sub = '手机正在连接本机服务';
  } else if (sess?.status === 'starting') {
    title = sess.mode === 'training' ? '摄像头正在转向训练位…' : '摄像头正在转向客厅…';
    sub = '通常需要 3–10 秒，摄像头确认到位后才开始记录';
    tone = 'wait';
  } else if (sess?.status === 'observing') {
    tone = 'on';
    title = sess.mode === 'training' ? '训练位已就绪' : `正在观察 · ${sess.label === 'alone' ? '我一个人' : '有人陪'}`;
    sub = v?.observable ? '看得清' : `现在看不到您：${REASON_TEXT[v?.reason || ''] || '—'}。这段时间不计入。`;
    if (sess.preset_ok === false) sub += '（没有收到摄像头确认，请看一下摄像头）';
  } else if (sess?.status === 'paused') {
    tone = 'pause';
    title = '已暂停';
    sub = sess.preset_ok === true ? '摄像头已转开（设备已确认）' : sess.preset_ok === false
      ? '没有收到摄像头确认，请看一下摄像头' : '摄像头正在转向墙面…';
  }

  const lastSts = today?.sts?.last;
  const prevSts = today?.sts?.previous_same_condition;

  return (
    <div className="elder">
      <div className={`status status--${tone}`}>
        <div className="status__title">{title}</div>
        <div className="status__sub">{sub}</div>
        {state?.bridge && !state.bridge.connected && <div className="status__warn">手机与本机断开，摄像头暂时无法控制</div>}
      </div>

      {stsRunning && sts && (
        <div className="sts">
          <div className="sts__prompt">{sts.prompt}</div>
          {sts.phase === 'running' || sts.phase === 'countdown' ? (
            <div className="sts__big">{sts.phase === 'running' ? sts.reps : Math.ceil(sts.remaining_s ?? 0)}</div>
          ) : null}
          {sts.phase === 'running' && <div className="sts__sub">剩余 {Math.ceil(sts.remaining_s ?? 0)} 秒</div>}
          <button className="big big--ghost" onClick={() => go(() => api('/api/training/sts/cancel', { method: 'POST' }))}>
            取消
          </button>
        </div>
      )}

      {!stsRunning && (
        <div className="actions">
          {!active && (
            <>
              <div className="seg">
                <button className={label === 'alone' ? 'on' : ''} onClick={() => setLabel('alone')}>我一个人</button>
                <button className={label === 'companion' ? 'on' : ''} onClick={() => setLabel('companion')}>有人陪</button>
              </div>
              <button className="big" onClick={() => go(() => api('/api/sessions', { method: 'POST', json: { mode: 'daily', label } }))}>
                开始观察
              </button>
              <button className="big big--alt" onClick={() => go(() => api('/api/sessions', { method: 'POST', json: { mode: 'training', label } }))}>
                开始训练
              </button>
            </>
          )}
          {active && sess?.mode === 'training' && sess.status === 'observing' && (
            <button className="big" onClick={() => go(() => api('/api/training/sts/start', { method: 'POST' }))}>
              开始 30 秒坐站
            </button>
          )}
          {active && sess?.status !== 'paused' && (
            <button className="big big--ghost" onClick={() => go(() => api('/api/sessions/current/pause', { method: 'POST' }))}>
              暂停（镜头转开）
            </button>
          )}
          {sess?.status === 'paused' && (
            <button className="big" onClick={() => go(() => api('/api/sessions/current/resume', { method: 'POST' }))}>
              继续
            </button>
          )}
          {active && (
            <button className="big big--ghost" onClick={() => go(() => api('/api/sessions/current/stop', { method: 'POST' }))}>
              结束
            </button>
          )}
        </div>
      )}

      <div className="card2">
        <div className="card2__title">今天</div>
        {!today || today.sessions.length === 0 ? (
          <div className="muted">今天还没有开启观察。</div>
        ) : (
          today.sessions
            .slice()
            .reverse()
            .map((s) => (
              <div className="rec" key={s.id}>
                <div className="rec__head">
                  {s.mode === 'training' ? '训练' : '日常观察'} · {fmtTime(s.started_at)}
                </div>
                <div>{s.sentence}</div>
                {s.mode === 'daily' && s.longest_sit_s ? (
                  <div className="muted">
                    最长连续坐着 {fmtDur(s.longest_sit_s)}
                    {s.longest_sit_truncated ? '（中间有没看到的时间，实际可能更长）' : ''}
                  </div>
                ) : null}
              </div>
            ))
        )}
        {lastSts && (
          <div className="rec">
            <div className="rec__head">30 秒坐站</div>
            <div className="sts-cmp">
              <div>
                <div className="sts-cmp__n">{lastSts.reps}</div>
                <div className="muted">这次{lastSts.is_sample ? '（示例）' : ''}{lastSts.valid ? '' : '（无效）'}</div>
              </div>
              {prevSts && (
                <div>
                  <div className="sts-cmp__n muted">{prevSts.reps}</div>
                  <div className="muted">上次同条件{prevSts.is_sample ? '（示例数据）' : ''}</div>
                </div>
              )}
            </div>
            <div className="muted small">只和同一把椅子、同一扶手规则的记录对比，不判断好坏。</div>
          </div>
        )}
      </div>

      <div className="card2">
        <div className="card2__title">分享给家属</div>
        <div className="seg">
          <button className={share?.level === 'none' ? 'on' : ''} onClick={() => go(() => api('/api/share', { method: 'PUT', json: { level: 'none' } }))}>
            不分享
          </button>
          <button className={share?.level === 'summary' ? 'on' : ''} onClick={() => go(() => api('/api/share', { method: 'PUT', json: { level: 'summary' } }))}>
            一句话 + 数字
          </button>
        </div>
        <div className="muted small">
          家属能看到：本周练了几天、日常起身次数和看得清的时长（分有人陪 / 独处）、30 秒坐站与上次对比。看不到画面、角度和疼痛。
        </div>
        {log.length > 0 && (
          <div className="muted small">
            最近访问：{log.slice(0, 3).map((x) => `${fmtTime(x.at)} ${x.allowed ? '家属查看' : '被拒绝'}`).join('；')}
          </div>
        )}
      </div>

      <button className="link-danger" onClick={() => setConfirmDelete(true)}>删除今天的记录</button>

      {ask && (
        <div className="modal">
          <div className="modal__box">
            <div className="modal__title">刚才站起来时旁边有人。有人扶您吗？</div>
            <div className="modal__row">
              {[['yes', '有人扶'], ['no', '没有扶'], ['unsure', '不确定']].map(([k, t]) => (
                <button key={k} className="big" onClick={() => go(async () => {
                  await api(`/api/rise_events/${ask.id}/confirm`, { method: 'POST', json: { assist: k } });
                  setAsk(null);
                })}>{t}</button>
              ))}
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div className="modal">
          <div className="modal__box">
            <div className="modal__title">删除今天的全部记录？删除后家属端也看不到。</div>
            <div className="modal__row">
              <button className="big big--danger" onClick={() => go(async () => {
                await api('/api/data?scope=today&confirm=true', { method: 'DELETE' });
                setConfirmDelete(false);
              })}>删除</button>
              <button className="big big--ghost" onClick={() => setConfirmDelete(false)}>取消</button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

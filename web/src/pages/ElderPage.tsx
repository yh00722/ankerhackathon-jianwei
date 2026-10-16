/**
 * @name 老人端
 * @description 嵌在安卓 App 的 WebView 里。大字、少按钮：开始 / 暂停 / 结束观察，训练（康复师计划里打开的动作），
 * 久坐提醒，今天的记录，分享设置（家属 / 康复师分开授权）。所有状态以设备确认为准：摄像头没确认转到位，
 * 就不显示「正在观察」。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, exerciseActive, fmtDur, fmtTime, LiveEvent, planLabel, REASON_TEXT, useLive } from '../api';
import { ExerciseCard } from '../components/ExerciseCard';
import { buzz, loadElderVoice, nativeVoiceReady, saveElderVoice, say } from '../utils/voice';

/** 训练、坐站、标定、久坐提醒的语音口令（手机 App 里用系统语音念） */
function voiceFor(e: LiveEvent): string {
  switch (e.kind) {
    case 'ex_phase':
      if (e.phase === 'prep' || e.phase === 'between') return e.prompt || '';
      if (e.phase === 'running') return e.exercise === 'balance' ? '开始计时，保持住' : `开始识别。${e.prompt || ''}`;
      if (e.phase === 'countdown') return '准备，三，二，一';
      if (e.phase === 'aborted') return '已取消';
      return '';
    case 'ex_rep':
      if (!e.counted) return '这次没看全，不算';
      if (e.exercise === 'march2min') return (e.reps ?? 0) > 0 && (e.reps ?? 0) % 10 === 0 ? `${e.reps} 次` : ''; // 太快，每 10 次报一次
      if (e.exercise === 'weight_shift') return String(e.reps ?? '');
      return `第 ${e.reps} 次`;
    case 'ex_attempt':
      return `保持了 ${Math.round(e.hold_s ?? 0)} 秒。刚才有没有扶东西？`;
    case 'ex_note':
    case 'ex_result':
    case 'reminder':
      return e.text || '';
    case 'sts_phase':
      if (e.phase === 'countdown') return '准备，三，二，一';
      if (e.phase === 'running') return '开始！尽量多次站起、坐下';
      return e.phase === 'done' ? '' : e.prompt || '';
    case 'sts_rep':
      return String(e.reps);
    case 'sts_result':
      return `完成，${e.reps} 次`;
    case 'calib':
      return e.phase === 'failed' ? `标定没有完成。${e.error || ''}` : e.prompt || '';
    default:
      return '';
  }
}

/** 训练中看不到人时，告诉本人该怎么调整（镜头转动时不提示，等它停下） */
const UNSEEN_HINT: Record<string, string> = {
  NO_PERSON: '看不到您，请回到画面中间',
  LOW_QUALITY: '看不清您的腿，请让膝盖和脚都在画面里',
  AMBIGUOUS: '旁边有人，分不清是谁，请旁人先离开画面',
  INVERTED: '画面是倒的，请家人在电脑上把画面转过来',
  STREAM_DOWN: '摄像头暂时断开，请稍等',
};

type Today = Awaited<ReturnType<typeof loadToday>>;

function loadToday() {
  return api<{
    sessions: any[];
    total: { on_s: number; observable_s: number; rises_complete: number; rises_incomplete: number };
    sts: { last: any; previous_same_condition: any } | null;
    exercises: { id: number; kind: string; at: number; line: string; valid: boolean }[];
    reminders: { count: number; stood_up: number };
  }>('/api/elder/today');
}

export function ElderPage() {
  const [label, setLabel] = useState<'alone' | 'companion'>('alone');
  const [today, setToday] = useState<Today | null>(null);
  const [share, setShare] = useState<{ level: string; therapist_level: string } | null>(null);
  const [ask, setAsk] = useState<LiveEvent | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [toast, setToast] = useState('');
  const [log, setLog] = useState<{ who: string; at: number; allowed: number }[]>([]);
  const [busy, setBusy] = useState('');
  const [flash, setFlash] = useState('');
  const flashTimer = useRef<number | undefined>(undefined);
  const [exFlash, setExFlash] = useState('');
  const [supportAsk, setSupportAsk] = useState<LiveEvent | null>(null);
  const [reminder, setReminder] = useState<LiveEvent | null>(null);
  const [voiceOn, setVoiceOn] = useState(loadElderVoice);
  const voiceRef = useRef(voiceOn);
  voiceRef.current = voiceOn;
  const sayIf = (t: string) => {
    if (voiceRef.current && t) say(t);
  };

  const refresh = useCallback(() => {
    loadToday().then(setToday).catch(() => {});
    api('/api/share').then(setShare).catch(() => {});
    api('/api/access_log').then(setLog).catch(() => {});
  }, []);

  const showFlash = (text: string) => {
    setFlash(text);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(''), 5000);
  };

  const { state, connected } = useLive((e) => {
    sayIf(voiceFor(e));
    if ((e.kind === 'ex_rep' && e.counted) || e.kind === 'sts_rep') buzz(60);
    if (e.kind === 'rise' && e.other_person && e.id) setAsk(e);
    if (e.kind === 'rise' || e.kind === 'sts_result' || e.kind === 'session') refresh();
    // 刚识别到的动作立刻告诉本人（不用等列表刷新）
    if (e.kind === 'rise' && e.text) showFlash(e.text);
    if (e.kind === 'calib' && (e.phase === 'done' || e.phase === 'failed')) showFlash(e.text || '');
    if ((e.kind === 'ex_rep' || e.kind === 'ex_attempt' || e.kind === 'ex_note') && e.text) setExFlash(e.text);
    if (e.kind === 'ex_phase' && e.phase === 'running') setExFlash('');
    if (e.kind === 'ex_attempt' && e.ask_support) setSupportAsk(e);
    if (e.kind === 'ex_result') {
      refresh();
      showFlash(e.text || '');
    }
    if (e.kind === 'reminder') setReminder(e);
    if (e.kind === 'reminder_end') {
      setReminder((r) => (r && r.id === e.id ? null : r));
      if (e.by === 'stood_up') showFlash('站起来了，很好');
    }
  });

  useEffect(() => {
    refresh();
    const iv = window.setInterval(refresh, 8000);
    return () => window.clearInterval(iv);
  }, [refresh]);

  /** 点了按钮立刻有反应（按钮变成「请稍候」、防止连点），请求回来再以服务端状态为准 */
  const go = async (fn: () => Promise<any>, pending = '请稍候…') => {
    setBusy(pending);
    try {
      await fn();
      refresh();
    } catch (e: any) {
      setToast(e.message);
      window.setTimeout(() => setToast(''), 4000);
    } finally {
      setBusy('');
    }
  };

  const sess = state?.session;
  const active = sess && sess.status !== 'ended';
  const v = state?.view;
  const sts = state?.sts;
  const stsRunning = sts && !['done', 'aborted'].includes(sts.phase);
  const cal = state?.calib;
  const calRunning = cal && (cal.phase === 'sit' || cal.phase === 'stand');
  const ex = state?.exercise;
  const exRunning = exerciseActive(ex);
  const plan = (state?.plan || []).filter((x) => x.enabled);
  const idle = !stsRunning && !calRunning && !exRunning;

  // 保持时报数：伸膝 / 屈膝「1、2、3，好，放下」；站立平衡每 5 秒报一次，到目标说「好」
  const holdSpoken = useRef({ k: 0, done: false });
  const holding = ex?.phase === 'running' ? ex.holding_s : null;
  useEffect(() => {
    if (!ex || holding == null) {
      holdSpoken.current = { k: 0, done: false };
      return;
    }
    const hs = holdSpoken.current;
    if (ex.exercise === 'knee_ext' || ex.exercise === 'knee_flex') {
      const target = ex.hold_target_s ?? 3;
      if (!hs.done && holding >= target) {
        hs.done = true;
        sayIf('好，放下');
        buzz(150);
      } else if (Math.floor(holding) > hs.k && holding < target) {
        hs.k = Math.floor(holding);
        sayIf(String(hs.k));
      }
    } else if (ex.exercise === 'balance') {
      const target = ex.target_s ?? 10;
      const k = Math.floor(holding / 5) * 5;
      if (!hs.done && holding >= target) {
        hs.done = true;
        sayIf(`${target} 秒，好`);
        buzz(150);
      } else if (k > hs.k && k < target) {
        hs.k = k;
        sayIf(`${k} 秒`);
      }
    }
  }, [holding, ex?.exercise]);

  // 训练中连续 2 秒看不到人：屏幕上写原因，并念一次（10 秒内不重复）
  const unseenSince = useRef<number | null>(null);
  const lastWarn = useRef(0);
  const training = exRunning || !!stsRunning;
  const unseenHint = training && v && !v.observable ? UNSEEN_HINT[v.reason || ''] || '' : '';
  useEffect(() => {
    if (!unseenHint) {
      unseenSince.current = null;
      return;
    }
    const now = Date.now();
    unseenSince.current ??= now;
    if (now - unseenSince.current > 2000 && now - lastWarn.current > 10000) {
      lastWarn.current = now;
      sayIf(unseenHint);
    }
  }, [unseenHint, state?.server_time]);

  const toggleVoice = () => {
    const next = !voiceOn;
    setVoiceOn(next);
    saveElderVoice(next);
    if (next) say(nativeVoiceReady() ? '语音提示已打开' : '语音提示已打开');
  };

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
    sub = !v?.observable
      ? `现在看不到您：${REASON_TEXT[v?.reason || ''] || '—'}。这段时间不计入。`
      : v.posture?.raw === 'transition' || (v.posture?.raw === 'stand' && !v.posture.stand_stable)
        ? '正在识别动作…'
        : '看得清';
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
        <button className="voice-btn" onClick={toggleVoice} aria-label="语音提示开关">
          {voiceOn ? '🔊 语音开' : '🔈 语音关'}
        </button>
        <div className="status__title">{title}</div>
        <div className="status__sub">{sub}</div>
        {state?.bridge && !state.bridge.connected && <div className="status__warn">手机与本机断开，摄像头暂时无法控制</div>}
        {state?.bridge?.status?.tracking === true && (
          <div className="status__warn">镜头会跟着您转（AI 追踪已打开）；转动时的动作不计入。暂停或结束时会自动关闭。</div>
        )}
      </div>

      {flash && <div className="flash">{flash}</div>}

      {calRunning && cal && (
        <div className="sts">
          <div className="sts__prompt">{cal.prompt}</div>
          <div className="progress"><div className="progress__bar" style={{ width: `${Math.round(cal.progress * 100)}%` }} /></div>
          <div className="sts__sub">标定这个位置（{cal.phase === 'sit' ? '第 1 步：坐 3 秒' : '第 2 步：站 3 秒'}），之后识别更准</div>
          <button className="big big--ghost" onClick={() => go(() => api('/api/calibration/cancel', { method: 'POST' }))}>
            取消
          </button>
        </div>
      )}

      {exRunning && ex && (
        <ExerciseCard ex={ex} flash={exFlash} warn={unseenHint}
          onCancel={() => go(() => api('/api/training/exercise/cancel', { method: 'POST' }))} />
      )}

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

      {idle && busy && <div className="actions"><button className="big" disabled>{busy}</button></div>}

      {idle && !busy && (
        <div className="actions">
          {!active && (
            <>
              <div className="seg">
                <button className={label === 'alone' ? 'on' : ''} onClick={() => setLabel('alone')}>我一个人</button>
                <button className={label === 'companion' ? 'on' : ''} onClick={() => setLabel('companion')}>有人陪</button>
              </div>
              <button className="big" onClick={() => go(() => api('/api/sessions', { method: 'POST', json: { mode: 'daily', label } }), '正在开始观察…')}>
                开始观察
              </button>
              <button className="big big--alt" onClick={() => go(() => api('/api/sessions', { method: 'POST', json: { mode: 'training', label } }), '正在开始训练…')}>
                开始训练
              </button>
            </>
          )}
          {active && sess?.mode === 'training' && sess.status === 'observing' && (
            <>
              {plan.length === 0 && <div className="muted">康复师的训练计划里还没有打开任何动作。</div>}
              {plan.map((x) => (
                <button key={x.kind} className="big"
                  onClick={() => go(() => api(x.kind === 'sts30' ? '/api/training/sts/start' : `/api/training/exercise/${x.kind}/start`,
                    { method: 'POST' }), '正在准备…')}>
                  {planLabel(x)}
                </button>
              ))}
              <div className="muted small">动作和目标由康复师在训练计划里设定。</div>
            </>
          )}
          {active && sess?.status !== 'paused' && (
            <button className="big big--ghost" onClick={() => go(() => api('/api/sessions/current/pause', { method: 'POST' }), '正在暂停…')}>
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
        {today && today.exercises.length > 0 && (
          <div className="rec">
            <div className="rec__head">训练</div>
            {today.exercises.slice().reverse().map((x) => (
              <div key={x.id}>{fmtTime(x.at)} {x.line}</div>
            ))}
          </div>
        )}
        {today && today.reminders.count > 0 && (
          <div className="rec">
            <div className="rec__head">久坐提醒</div>
            <div>提醒了 {today.reminders.count} 次，其中 {today.reminders.stood_up} 次提醒后站起来了。</div>
          </div>
        )}
        {lastSts && (
          <div className="rec">
            <div className="rec__head">30 秒坐站</div>
            <div className="sts-cmp">
              <div>
                <div className="sts-cmp__n">{lastSts.reps}</div>
                <div className="muted">最近一次{lastSts.is_sample ? '（示例数据）' : ''}{lastSts.valid ? '' : '（无效）'}</div>
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
        <div className="card2__title" style={{ marginTop: 16 }}>分享给康复师</div>
        <div className="seg">
          <button className={share?.therapist_level !== 'full' ? 'on' : ''}
            onClick={() => go(() => api('/api/share', { method: 'PUT', json: { therapist_level: 'none' } }))}>
            不分享
          </button>
          <button className={share?.therapist_level === 'full' ? 'on' : ''}
            onClick={() => go(() => api('/api/share', { method: 'PUT', json: { therapist_level: 'full' } }))}>
            训练与活动数据
          </button>
        </div>
        <div className="muted small">
          康复师能看到：每天的活动和看得清的时长、每个训练动作的次数和保持秒数（分左右腿）、实验性角度。看不到画面。
        </div>
        {log.length > 0 && (
          <div className="muted small">
            最近访问：{log.slice(0, 3).map((x) => `${fmtTime(x.at)} ${x.allowed ? (x.who === 'therapist' ? '康复师查看' : '家属查看')
              : `${x.who === 'therapist' ? '康复师' : x.who === 'family' ? '家属' : '未知链接'}被拒绝`}`).join('；')}
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

      {supportAsk && (
        <div className="modal">
          <div className="modal__box">
            <div className="modal__title">{supportAsk.text}。刚才有没有扶东西？</div>
            <div className="modal__row">
              {[['yes', '扶了'], ['no', '没扶']].map(([k, t]) => (
                <button key={k} className="big" onClick={() => go(async () => {
                  await api('/api/training/exercise/self_report', { method: 'POST', json: { support: k } });
                  setSupportAsk(null);
                })}>{t}</button>
              ))}
            </div>
            <button className="link-danger" onClick={() => setSupportAsk(null)}>不回答</button>
          </div>
        </div>
      )}

      {reminder && (
        <div className="modal">
          <div className="modal__box">
            <div className="modal__title">{reminder.text}</div>
            <div className="modal__row">
              <button className="big" onClick={() => go(async () => {
                await api(`/api/reminders/${reminder.id}/respond`, { method: 'POST', json: { response: 'ok' } });
                setReminder(null);
              })}>好，我起来走走</button>
              <button className="big big--ghost" onClick={() => go(async () => {
                await api(`/api/reminders/${reminder.id}/respond`, { method: 'POST', json: { response: 'later' } });
                setReminder(null);
              })}>等一会儿</button>
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

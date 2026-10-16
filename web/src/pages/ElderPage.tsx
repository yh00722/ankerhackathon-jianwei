/**
 * @name 老人端
 * @description 嵌在安卓 App 的 WebView 里，给术后老人自己用。所有状态以设备确认为准：摄像头没确认转到位，
 * 就不显示「正在观察」。
 *
 * 适老化（参照工信部《移动互联网应用（APP）适老化通用设计规范》）：
 * - 一屏一件事：最上面是状态（摄像头在不在看 + 一句大白话），下面只有当前能做的 1–2 个大按钮；
 *   做训练时进入专注模式，记录和设置都收起来；分享、删除收进「分享与隐私」。
 * - 正文 20 号起、标题 30 号；按钮不低于 64 像素（主按钮 76）；文字用深灰，不用浅灰。
 * - 每个操作都有即时反馈：按钮变「请稍候」、大字提示、语音（手机 App 里用系统语音）、振动。
 * - 用词不用技术词：「镜头转开」「看得清 / 看不到您」「只有我 / 有人陪着」。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, EX_NAME, exerciseActive, fmtDur, fmtTime, LiveEvent, PlanItem, REASON_TEXT, useLive, VARIANT_TEXT } from '../api';
import { ExerciseCard } from '../components/ExerciseCard';
import { buzz, loadElderVoice, saveElderVoice, say } from '../utils/voice';

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

const EX_ICON: Record<string, string> = {
  sts30: '🪑', knee_ext: '🦵', knee_flex: '🦵', balance: '🧍', march2min: '🚶', weight_shift: '↔️',
};

/** 训练动作按钮的第二行：只说要做什么，不堆参数 */
function exSub(p: PlanItem): string {
  const x = p.params || {};
  switch (p.kind) {
    case 'sts30':
      return '30 秒内尽量多次站起、坐下';
    case 'knee_ext':
    case 'knee_flex':
      return `${x.target_reps ?? 10} 次，每次保持 ${x.hold_s ?? 3} 秒`;
    case 'balance':
      return `${VARIANT_TEXT[x.variant] || '站稳'}，目标 ${x.target_s ?? 10} 秒`;
    case 'march2min':
      return `扶着台面，原地抬膝 ${Math.round((x.duration_s ?? 120) / 60)} 分钟`;
    case 'weight_shift':
      return `扶着台面，左右移 ${x.target_reps ?? 10} 次`;
    default:
      return '';
  }
}

/** 「今天」大数字用的时长：不到 1 分钟、N 分钟、X 小时 Y 分 */
function bigDur(s: number): string {
  if (s < 60) return s > 0 ? '不到 1 分钟' : '0 分钟';
  return fmtDur(s);
}

function sameDay(a: number, b: number): boolean {
  const f = (t: number) => new Date(t * 1000).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' });
  return f(a) === f(b);
}

type Today = Awaited<ReturnType<typeof loadToday>>;

function loadToday() {
  return api<{
    sessions: any[];
    total: { on_s: number; observable_s: number; rises_complete: number; rises_incomplete: number };
    sts: { last: any; previous_same_condition: any; history?: { at: number; is_sample: boolean }[] } | null;
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
    if (next) say('声音提示已打开');
  };

  // ---- 状态：摄像头在不在看 + 一句大字 + 一句说明 + 至多一条提醒 ----
  const where = sess?.mode === 'training' ? '训练椅' : '客厅';
  let tone: 'off' | 'wait' | 'on' | 'pause' = 'off';
  let eye = '🙈';
  let title = '观察已关闭';
  let sub = '摄像头现在不看您。需要时点下面的按钮。';
  if (!connected) {
    tone = 'wait';
    eye = '⏳';
    title = '正在连接…';
    sub = '请稍等几秒';
  } else if (sess?.status === 'starting') {
    tone = 'wait';
    eye = '⏳';
    title = `摄像头正在转向${where}…`;
    sub = '转好以后才开始记录，一般要 3–10 秒';
  } else if (sess?.status === 'observing') {
    tone = 'on';
    eye = '👁';
    title = sess.mode === 'training' ? '已对准训练椅' : '正在看着客厅';
    if (!v?.observable) sub = `现在看不到您（${REASON_TEXT[v?.reason || ''] || '—'}），这段时间不计入`;
    else if (v.posture?.raw === 'transition' || (v.posture?.raw === 'stand' && !v.posture.stand_stable)) sub = '正在识别动作…';
    else sub = sess.mode === 'training' ? '看得清您，选一个动作开始' : `看得清您 · ${sess.label === 'alone' ? '只有我' : '有人陪着'}`;
  } else if (sess?.status === 'paused') {
    tone = 'pause';
    title = '已暂停';
    sub = sess.preset_ok === true ? '镜头已经转向墙面' : sess.preset_ok === false ? '没收到摄像头回应，请看一下摄像头'
      : '镜头正在转向墙面…';
  }
  const warn = state?.bridge && !state.bridge.connected ? '手机和电脑断开了，暂时控制不了摄像头'
    : sess?.status === 'observing' && sess.preset_ok === false ? '没收到摄像头回应，请看一下摄像头有没有转好'
      : state?.bridge?.status?.tracking === true ? '镜头会跟着您转，转动时的动作不计入'
        : '';
  const focus = exRunning || !!stsRunning || !!calRunning; // 做动作时只留状态和动作卡片

  const lastSts = today?.sts?.last;
  const prevSts = today?.sts?.previous_same_condition;
  const now = Date.now() / 1000;
  const stsToday = (today?.sts?.history || []).filter((r) => !r.is_sample && sameDay(r.at, now)).length;
  const trainingsToday = (today?.exercises?.length || 0) + stsToday;

  const post = (path: string, json?: any) => () => api(path, { method: 'POST', ...(json ? { json } : {}) });

  return (
    <div className="elder">
      <header className="e-top">
        <span className="e-brand">见微知行</span>
        <button className={`e-voice${voiceOn ? ' on' : ''}`} onClick={toggleVoice} aria-pressed={voiceOn}>
          {voiceOn ? '🔊 声音开' : '🔇 声音关'}
        </button>
      </header>

      <section className={`e-status e-status--${tone}`} role="status" aria-live="polite">
        <div className="e-status__row">
          <span className={`e-status__eye${tone === 'wait' ? ' e-pulse' : ''}`} aria-hidden>{eye}</span>
          <div>
            <div className="e-status__title">{title}</div>
            <div className="e-status__sub">{sub}</div>
          </div>
        </div>
        {warn && <div className="e-status__warn">⚠️ {warn}</div>}
      </section>

      {flash && <div className="flash" role="status">{flash}</div>}

      {calRunning && cal && (
        <div className="sts">
          <div className="ex__name">标定这个位置（{cal.phase === 'sit' ? '第 1 步：坐 3 秒' : '第 2 步：站 3 秒'}）</div>
          <div className="sts__prompt">{cal.prompt}</div>
          <div className="progress"><div className="progress__bar" style={{ width: `${Math.round(cal.progress * 100)}%` }} /></div>
          <div className="sts__sub">标定以后识别更准</div>
          <button className="e-btn e-btn--ghost" onClick={() => go(post('/api/calibration/cancel'))}>取消</button>
        </div>
      )}

      {exRunning && ex && (
        <ExerciseCard ex={ex} flash={exFlash} warn={unseenHint}
          onCancel={() => go(post('/api/training/exercise/cancel'))} />
      )}

      {stsRunning && sts && (
        <div className="sts">
          <div className="ex__name">30 秒坐站</div>
          <div className="sts__prompt">{sts.prompt}</div>
          {sts.phase === 'running' || sts.phase === 'countdown' ? (
            <div className="sts__big">{sts.phase === 'running' ? sts.reps : Math.ceil(sts.remaining_s ?? 0)}</div>
          ) : null}
          {sts.phase === 'running' && <div className="sts__sub">次 · 还剩 {Math.ceil(sts.remaining_s ?? 0)} 秒</div>}
          {unseenHint && <div className="ex__warn">{unseenHint}</div>}
          <button className="e-btn e-btn--ghost" onClick={() => go(post('/api/training/sts/cancel'))}>取消</button>
        </div>
      )}

      {!focus && (
        <section className="e-actions">
          {busy ? (
            <button className="e-btn" disabled>{busy}</button>
          ) : (
            <>
              {!active && (
                <>
                  <div className="e-q">现在有人陪着您吗？</div>
                  <div className="e-choice">
                    <button className={label === 'alone' ? 'on' : ''} aria-pressed={label === 'alone'} onClick={() => setLabel('alone')}>
                      🧍<span>只有我</span>
                    </button>
                    <button className={label === 'companion' ? 'on' : ''} aria-pressed={label === 'companion'}
                      onClick={() => setLabel('companion')}>
                      👥<span>有人陪着</span>
                    </button>
                  </div>
                  <button className="e-btn" onClick={() => go(post('/api/sessions', { mode: 'daily', label }), '正在开始…')}>
                    开始观察
                  </button>
                  <button className="e-btn e-btn--alt" onClick={() => go(post('/api/sessions', { mode: 'training', label }), '正在开始…')}>
                    开始训练
                  </button>
                </>
              )}
              {sess?.status === 'observing' && sess.mode === 'training' && (
                <div className="e-exlist">
                  {plan.length === 0 && <div className="e-note">康复师还没有安排训练动作。</div>}
                  {plan.map((x) => (
                    <button key={x.kind} className="e-ex"
                      onClick={() => go(post(x.kind === 'sts30' ? '/api/training/sts/start' : `/api/training/exercise/${x.kind}/start`), '正在准备…')}>
                      <span className="e-ex__icon" aria-hidden>{EX_ICON[x.kind] || '•'}</span>
                      <span className="e-ex__text"><b>{EX_NAME[x.kind] || x.kind}</b><span>{exSub(x)}</span></span>
                      <span className="e-ex__go" aria-hidden>›</span>
                    </button>
                  ))}
                </div>
              )}
              {active && sess?.status !== 'paused' && (
                <button className="e-btn e-btn--ghost" onClick={() => go(post('/api/sessions/current/pause'), '正在暂停…')}>
                  ⏸ 暂停（镜头转开）
                </button>
              )}
              {sess?.status === 'paused' && (
                <button className="e-btn" onClick={() => go(post('/api/sessions/current/resume'), '正在继续…')}>▶ 继续</button>
              )}
              {active && (
                <button className="e-btn e-btn--plain" onClick={() => go(post('/api/sessions/current/stop'))}>
                  结束{sess?.mode === 'training' ? '训练' : '观察'}
                </button>
              )}
            </>
          )}
        </section>
      )}

      {!focus && (
        <section className="card2 e-today">
          <h2 className="e-h2">今天</h2>
          <div className="e-stats">
            <div><b>{today?.total.rises_complete ?? 0}</b><span>次完整站起来</span></div>
            <div><b>{bigDur(today?.total.observable_s ?? 0)}</b><span>看得清</span></div>
            <div><b>{trainingsToday}</b><span>次训练</span></div>
          </div>
          {lastSts && (
            <div className="rec">
              <div className="rec__head">30 秒坐站</div>
              <div className="sts-cmp">
                <div>
                  <div className="sts-cmp__n">{lastSts.reps}</div>
                  <div className="e-note">最近一次{lastSts.is_sample ? '（示例数据）' : ''}{lastSts.valid ? '' : '（无效）'}</div>
                </div>
                {prevSts && (
                  <div>
                    <div className="sts-cmp__n e-dim">{prevSts.reps}</div>
                    <div className="e-note">上次同样条件{prevSts.is_sample ? '（示例数据）' : ''}</div>
                  </div>
                )}
              </div>
              <div className="e-note">只和同一把椅子、同样扶手规则的记录放在一起，不评判好坏。</div>
            </div>
          )}
          <details className="e-more">
            <summary>看每一段的记录</summary>
            {!today || today.sessions.length === 0 ? (
              <div className="e-note">今天还没有开始观察。</div>
            ) : (
              today.sessions.slice().reverse().map((s) => (
                <div className="rec" key={s.id}>
                  <div className="rec__head">{s.mode === 'training' ? '训练' : '日常观察'} · {fmtTime(s.started_at)}</div>
                  <div>{s.sentence}</div>
                  {s.mode === 'daily' && s.longest_sit_s ? (
                    <div className="e-note">
                      最长连续坐了 {fmtDur(s.longest_sit_s)}{s.longest_sit_truncated ? '（中间有没看到的时间，实际可能更长）' : ''}
                    </div>
                  ) : null}
                </div>
              ))
            )}
            {today && today.exercises.length > 0 && (
              <div className="rec">
                <div className="rec__head">训练</div>
                {today.exercises.slice().reverse().map((x) => <div key={x.id}>{fmtTime(x.at)} {x.line}</div>)}
              </div>
            )}
            {today && today.reminders.count > 0 && (
              <div className="rec">
                <div className="rec__head">久坐提醒</div>
                <div>提醒了 {today.reminders.count} 次，其中 {today.reminders.stood_up} 次提醒后站起来了。</div>
              </div>
            )}
          </details>
        </section>
      )}

      {!focus && (
        <details className="card2 e-settings">
          <summary className="e-h2">分享与隐私</summary>
          <div className="e-set">
            <div className="e-set__label">给家人看</div>
            <div className="e-choice e-choice--sm">
              <button className={share?.level !== 'summary' ? 'on' : ''} onClick={() => go(() => api('/api/share', { method: 'PUT', json: { level: 'none' } }))}>
                不给看
              </button>
              <button className={share?.level === 'summary' ? 'on' : ''} onClick={() => go(() => api('/api/share', { method: 'PUT', json: { level: 'summary' } }))}>
                一句话 + 数字
              </button>
            </div>
            <p className="e-note">家人能看到：本周练了几天、站起来几次、看得清多久、30 秒坐站和上次比。看不到画面。</p>
          </div>
          <div className="e-set">
            <div className="e-set__label">给康复师看</div>
            <div className="e-choice e-choice--sm">
              <button className={share?.therapist_level !== 'full' ? 'on' : ''}
                onClick={() => go(() => api('/api/share', { method: 'PUT', json: { therapist_level: 'none' } }))}>
                不给看
              </button>
              <button className={share?.therapist_level === 'full' ? 'on' : ''}
                onClick={() => go(() => api('/api/share', { method: 'PUT', json: { therapist_level: 'full' } }))}>
                训练和活动
              </button>
            </div>
            <p className="e-note">康复师能看到：每天的活动、每个训练动作的次数和保持秒数。看不到画面。</p>
          </div>
          {log.length > 0 && (
            <p className="e-note">
              最近谁看过：{log.slice(0, 3).map((x) => `${fmtTime(x.at)} ${x.allowed ? (x.who === 'therapist' ? '康复师' : '家人')
                : `${x.who === 'therapist' ? '康复师' : x.who === 'family' ? '家人' : '不认识的链接'}（没让看）`}`).join('；')}
            </p>
          )}
          <p className="e-note">🔒 不存画面和照片，只存次数和时长。</p>
          <button className="e-btn e-btn--danger-ghost" onClick={() => setConfirmDelete(true)}>删除今天的记录</button>
        </details>
      )}

      {ask && (
        <div className="modal">
          <div className="modal__box">
            <div className="modal__title">刚才站起来时旁边有人。有人扶您吗？</div>
            <div className="modal__row">
              {[['yes', '有人扶'], ['no', '没有扶'], ['unsure', '记不清']].map(([k, t]) => (
                <button key={k} className="e-btn" onClick={() => go(async () => {
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
                <button key={k} className="e-btn" onClick={() => go(async () => {
                  await api('/api/training/exercise/self_report', { method: 'POST', json: { support: k } });
                  setSupportAsk(null);
                })}>{t}</button>
              ))}
              <button className="e-btn e-btn--plain" onClick={() => setSupportAsk(null)}>不回答</button>
            </div>
          </div>
        </div>
      )}

      {reminder && (
        <div className="modal">
          <div className="modal__box">
            <div className="modal__title">🪑 {reminder.text}</div>
            <div className="modal__row">
              <button className="e-btn" onClick={() => go(async () => {
                await api(`/api/reminders/${reminder.id}/respond`, { method: 'POST', json: { response: 'ok' } });
                setReminder(null);
              })}>好，我起来走走</button>
              <button className="e-btn e-btn--plain" onClick={() => go(async () => {
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
            <div className="modal__title">删除今天的全部记录？删掉以后，家人那边也看不到了。</div>
            <div className="modal__row">
              <button className="e-btn e-btn--danger" onClick={() => go(async () => {
                await api('/api/data?scope=today&confirm=true', { method: 'DELETE' });
                setConfirmDelete(false);
              })}>删除</button>
              <button className="e-btn e-btn--plain" onClick={() => setConfirmDelete(false)}>不删了</button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className="toast" role="alert">{toast}</div>}
    </div>
  );
}

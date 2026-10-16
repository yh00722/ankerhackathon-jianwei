/**
 * @name 控制台
 * @description 投到大屏给评委看「识别结果」，同时负责把帧上传给分析服务。
 *
 * 展示（默认）：
 * - 左：AI 实时画面（缓冲前直取 + 骨架），画面上叠当前判断、训练口令、镜头转动；下面是最近几条识别记录
 * - 右：「老人手机上看到的」——直接嵌老人端页面（#/elder?mirror=1，和手机同步，不发声）
 * - 顶：设备信息一行（硬要求：设备控制 + 视频以外的信息）
 * 管理（⚙ 或按 M）：操作按钮、机位、标定、官方播放器、技术指标、调试——团队用，平时收起
 */

import { ReactNode, useEffect, useRef, useState } from 'react';
import { api, EX_NAME, exerciseActive, fmtTime, LiveEvent, POSTURE_TEXT, REASON_TEXT, speak, useLive } from '../api';
import { tapStats, useFrameUploader } from './useFrameUploader';

const BONES = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [24, 26], [26, 28], [27, 29], [29, 31], [28, 30], [30, 32],
];

const STATUS_TEXT: Record<string, string> = {
  starting: '摄像头转向中…',
  observing: '正在观察',
  paused: '已暂停（镜头转向墙面）',
  ended: '已结束',
};

const CMD_TEXT: Record<string, string> = {
  GOTO_PRESET: '转到预置位',
  SAVE_PRESET: '保存预置位',
  PTZ_STEP: '云台单步',
  SET_TRACKING: 'AI 追踪',
  SET_RESOLUTION: '直播画质',
  QUERY_STATUS: '查询状态',
  QUERY_EVENTS: '查询事件',
};

function cmd(type: string, args: Record<string, any> = {}) {
  return api('/api/device/command', { method: 'POST', json: { type, args } });
}

const PRESET_OF_MODE: Record<string, number> = { daily: 1, training: 2 };

/** 直播画质：设备物模型没给选项时用演示工程里的默认枚举 */
const RES_FALLBACK = [
  { value: '1', desc: '720P' },
  { value: '2', desc: '1080P' },
  { value: '0', desc: '自动' },
];

/** 电脑念口令默认关：老人手机上会念（App 里用系统语音），两边同时念会串音。需要时在控制台打开。 */
function loadVoice(): boolean {
  try {
    return localStorage.getItem('jw.voice') === 'on';
  } catch {
    return false;
  }
}

/** 标定、30 秒坐站的口令用语音念出来（被测的人不用盯着屏幕） */
function voiceFor(e: LiveEvent): string {
  if (e.kind === 'calib') return e.phase === 'failed' ? `标定没有完成。${e.error || ''}` : e.prompt || '';
  if (e.kind === 'sts_phase') return e.phase === 'countdown' ? '准备，三，二，一' : e.prompt || '';
  if (e.kind === 'sts_rep') return String(e.reps);
  if (e.kind === 'sts_result') return `完成，${e.reps} 次`;
  if (e.kind === 'ex_phase') return e.phase === 'countdown' ? '准备，三，二，一' : e.phase === 'done' ? '' : e.prompt || '';
  if (e.kind === 'ex_rep') return e.counted ? (e.exercise === 'march2min' ? '' : String(e.reps ?? '')) : '这次没看全';
  if (e.kind === 'ex_attempt') return `保持了 ${Math.round(e.hold_s ?? 0)} 秒`;
  if (e.kind === 'ex_note') return e.text || '';
  if (e.kind === 'ex_result') return '完成';
  if (e.kind === 'reminder') return e.text || '';
  return '';
}

/** 页面上播放的视频在浏览器抖动缓冲里等了多久（requestVideoFrameCallback：presentationTime − receiveTime，毫秒） */
function useVideoBufferMs(): number | null {
  const [ms, setMs] = useState<number | null>(null);
  useEffect(() => {
    let xs: number[] = [];
    let video: HTMLVideoElement | null = null;
    let handle = 0;
    const cb = (_: number, m: any) => {
      if (m.receiveTime) xs.push(m.presentationTime - m.receiveTime);
      if (video) handle = video.requestVideoFrameCallback(cb);
    };
    const iv = window.setInterval(() => {
      const v = document.querySelector('video');
      if (v !== video) {
        if (video && handle) video.cancelVideoFrameCallback(handle);
        video = v;
        if (video?.requestVideoFrameCallback) handle = video.requestVideoFrameCallback(cb);
      }
      if (xs.length) {
        const sorted = xs.slice().sort((a, b) => a - b);
        setMs(Math.round(sorted[Math.floor(sorted.length / 2)]));
      } else setMs(null);
      xs = [];
    }, 2000);
    return () => {
      window.clearInterval(iv);
      if (video && handle) video.cancelVideoFrameCallback(handle);
    };
  }, []);
  return ms;
}

/** 事件流的图标：一眼分清完整看到、没看全、训练结果、设备 */
function evIcon(e: LiveEvent & Record<string, any>): string {
  switch (e.kind) {
    case 'rise':
      return e.complete ? '✅' : '⚠️';
    case 'session':
      return '📷';
    case 'sts_result':
      return '🪑';
    case 'ex_result':
      return '🏋️';
    case 'ex_rep':
      return e.counted ? '✔️' : '⚠️';
    case 'ex_attempt':
      return '🧍';
    case 'calib':
      return '📐';
    case 'reminder':
      return '⏰';
    case 'reminder_end':
      return '🚶';
    default:
      return 'ℹ️';
  }
}

export function ConsolePanel({ stationSn, streaming, onStall, player, stallCount = 0, onDisconnect }: {
  stationSn: string; streaming: boolean; onStall?: (why: 'stall' | 'no_start') => boolean;
  player?: ReactNode; stallCount?: number; onDisconnect?: () => void;
}) {
  const [voice, setVoice] = useState(loadVoice);
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  const { state, connected } = useLive((e) => {
    if (voiceRef.current) speak(voiceFor(e));
  });
  const lastBlob = useRef<Blob | null>(null);
  const upload = useFrameUploader(stationSn, streaming, (b) => (lastBlob.current = b), onStall);
  const videoBufferMs = useVideoBufferMs();
  const canvas = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const [familyToken, setFamilyToken] = useState('');
  const [therapistToken, setTherapistToken] = useState('');
  const [msg, setMsg] = useState('');
  const [manage, setManage] = useState(false);

  // 按 M 打开 / 收起管理面板（输入框里打字时不算）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if ((e.key === 'm' || e.key === 'M') && tag !== 'INPUT' && tag !== 'TEXTAREA' && !e.metaKey && !e.ctrlKey) {
        setManage((x) => !x);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    api('/api/share').then((s) => {
      setFamilyToken(s.family_token);
      setTherapistToken(s.therapist_token);
    }).catch(() => {});
  }, []);

  // 预览：每 200 毫秒画一次最新帧 + 骨架；按服务端的旋转设置把画面转正
  useEffect(() => {
    let busy = false;
    const iv = window.setInterval(async () => {
      const c = canvas.current;
      const blob = lastBlob.current;
      if (!c || !blob || busy) return;
      busy = true;
      try {
        const bmp = await createImageBitmap(blob);
        const ctx = c.getContext('2d')!;
        const rot = stateRef.current?.rotate ?? 0;
        const flip = rot === 180;
        c.width = 960; // 大屏投影：用直取帧的原始宽度画，不再放大模糊
        c.height = Math.round((960 * bmp.height) / bmp.width);
        ctx.save();
        if (flip) {
          ctx.translate(c.width, c.height);
          ctx.rotate(Math.PI);
        }
        ctx.drawImage(bmp, 0, 0, c.width, c.height);
        ctx.restore();
        bmp.close();
        const v = stateRef.current?.view;
        (v?.skeletons || []).forEach((sk, i) => {
          const pt = (k: number) => {
            const [x, y] = sk[k];
            return flip ? [(1 - x) * c.width, (1 - y) * c.height] : [x * c.width, y * c.height];
          };
          ctx.strokeStyle = i === v?.target ? '#3ddc97' : '#9aa4b2';
          ctx.lineWidth = i === v?.target ? 5 : 3;
          BONES.forEach(([a, b]) => {
            if (sk[a][2] < 0.4 || sk[b][2] < 0.4) return;
            const [x1, y1] = pt(a);
            const [x2, y2] = pt(b);
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
          });
        });
      } finally {
        busy = false;
      }
    }, 200);
    return () => window.clearInterval(iv);
  }, []);

  const run = async (fn: () => Promise<any>, ok?: string) => {
    try {
      await fn();
      if (ok) setMsg(ok);
    } catch (e: any) {
      setMsg(`失败：${e.message}`);
    }
  };

  const s = state;
  const v = s?.view;
  const b = s?.bridge;
  const st = b?.status || {};
  const sess = s?.session;
  const live = sess && sess.status !== 'ended';
  const th = s?.thresholds;
  const cal = s?.calib;
  const calActive = cal && (cal.phase === 'sit' || cal.phase === 'stand');
  const stsActive = s?.sts && !['done', 'aborted'].includes(s.sts.phase);
  const ex = s?.exercise;
  const exActive = exerciseActive(ex);
  const training = sess?.mode === 'training' && sess.status === 'observing';
  const planOn = (s?.plan || []).filter((x) => x.enabled && x.kind !== 'sts30');

  const resOpts: { value: string; desc: string }[] = st.resolution_options?.length ? st.resolution_options : RES_FALLBACK;
  function resName(v: string) {
    return resOpts.find((o) => o.value === String(v))?.desc || `档位 ${v}`;
  }

  const toggleVoice = () => {
    const next = !voice;
    setVoice(next);
    try {
      localStorage.setItem('jw.voice', next ? 'on' : 'off');
    } catch {
      /* 记不住也没关系 */
    }
    if (next) speak('语音提示已打开');
  };

  const bench = async () => {
    setMsg('模型测速中：收 40 帧，分别用 full / heavy 推理（约 10–20 秒）…');
    try {
      const r = await api('/api/debug/bench', { method: 'POST', json: { n: 40, models: ['lite', 'full', 'heavy'] } });
      const line = (k: string) => r[k] ? `${k} ${r[k].ms_p50} ms（≈${r[k].fps_max} 帧/秒，检出 ${Math.round(r[k].detect_rate * 100)}%` +
        `${r[k].f1_mad_vs_full != null && k !== 'full' ? `，f1 与 full 差 ${r[k].f1_mad_vs_full}` : ''}）` : '';
      setMsg(`测速 ${r.frames} 帧：` + ['lite', 'full', 'heavy'].map(line).filter(Boolean).join('；'));
    } catch (e: any) {
      setMsg(`测速失败：${e.message}`);
    }
  };

  // 画面上叠的判断：评委一眼看懂系统现在「看到了什么」
  const stateText = !s ? '分析服务没连上'
    : !v?.observable ? `没看到：${REASON_TEXT[v?.reason || ''] || v?.reason || '—'}`
      : `${POSTURE_TEXT[v.posture?.raw || 'unknown']}${v.other_near ? ' · 旁边有人' : ''}`;
  const prompt = calActive && cal ? `标定：${cal.prompt}（${Math.round(cal.progress * 100)}%）`
    : stsActive && s?.sts ? `30 秒坐站：${s.sts.prompt}${s.sts.phase === 'running' ? ` · ${s.sts.reps} 次 · 剩 ${Math.ceil(s.sts.remaining_s ?? 0)} 秒` : ''}`
      : exActive && ex ? `${ex.name}：${ex.prompt}${ex.reps != null && ex.phase === 'running' ? ` · ${ex.reps}${ex.target_reps ? ` / ${ex.target_reps}` : ''} 次` : ''}${ex.holding_s != null ? ` · 保持 ${ex.holding_s} 秒` : ''}`
        : '';
  const lastCmd = (b?.recent || []).slice().reverse().find((c) => c.state === 'done');
  const sessText = sess ? `${sess.mode === 'training' ? '训练' : '日常'} · ${STATUS_TEXT[sess.status]}` : '未开启观察（不记录）';
  const camLine = !b?.connected ? '手机设备桥断开'
    : [st.online === false ? '摄像头离线' : '摄像头在线', st.battery != null ? `电量 ${st.battery}%${st.charging ? '（充电中）' : ''}` : '',
      st.wifi_name ? `Wi-Fi ${st.wifi_name} ${st.wifi_level ?? '—'} 格` : '', lastCmd ? `云台确认 ${Math.round(lastCmd.latency_ms || 0)} ms` : '',
      (b?.events || []).length ? `设备事件 ${(b?.events || []).length} 条` : ''].filter(Boolean).join(' · ');
  const elderUrl = `${location.origin}${location.pathname}#/elder?mirror=1`;

  return (
    <div className="cx">
      <header className="cx-top">
        <div className="cx-title">见微知行</div>
        <span className={`cx-chip ${b?.connected && st.online !== false ? 'ok' : 'warn'}`}>📷 {camLine}</span>
        <span className={`cx-chip ${sess?.status === 'observing' ? 'ok' : ''}`}>{sessText}</span>
        {!connected && <span className="cx-chip warn">分析服务未连接</span>}
        <button className="cx-manage" onClick={() => setManage(true)} title="管理（按 M）">⚙ 管理</button>
      </header>

      {v?.inverted_hint && (
        <div className="console__msg console__msg--warn">
          画面里的人是倒着的（肩在髋下方）：摄像头可能倒装了。
          <button className="btn btn--small" onClick={() => run(() => api('/api/config', { method: 'POST', json: { rotate: s?.rotate === 180 ? 0 : 180 } }))}>
            把画面转 180°
          </button>
        </div>
      )}

      <div className="cx-stage">
        <section className="cx-view">
          <div className="cx-canvas-wrap">
            <canvas ref={canvas} className="cx-canvas" />
            <div className="cx-badge">AI 看到的画面 · 不存图</div>
            {prompt && <div className="cx-prompt">{prompt}</div>}
            <div className={`cx-state ${v?.observable ? 'ok' : 'warn'}`}>{stateText}</div>
            {b?.camera_moving && <div className="cx-moving">镜头转动中 · 这段不计入</div>}
          </div>
          <div className="cx-log">
            <div className="cx-log__title">识别记录</div>
            <ul>
              {(s?.events || []).length === 0 && <li className="muted">还没有记录</li>}
              {(s?.events || []).slice(0, 5).map((e, i) => (
                <li key={i}>
                  <span aria-hidden>{evIcon(e)}</span>
                  <span className="cx-ev__t">{fmtTime(e.at)}</span>
                  <span>{e.text}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <aside className="cx-phone">
          <div className="cx-phone__title">老人手机上看到的</div>
          <div className="cx-phone__frame">
            <iframe src={elderUrl} title="老人端（与手机同步）" />
          </div>
        </aside>
      </div>

      {/* 管理面板：团队操作用，平时收起。官方播放器也在这里（只是不显示，连接照常） */}
      <div className={`cx-drawer${manage ? ' open' : ''}`} aria-hidden={!manage}>
        <div className="cx-drawer__head">
          <b>管理</b>
          <span className="muted small">团队操作用 · 按 M 收起</span>
          <button className="btn btn--small btn--secondary" onClick={() => setManage(false)}>收起</button>
        </div>
        <div className="cx-drawer__body">
          <div className="cx-under">
            <div className="cx-player">
              {player}
              <div className="cx-cap">
                官方播放器{videoBufferMs != null ? `（浏览器缓冲 ${(videoBufferMs / 1000).toFixed(1)} 秒）` : ''}
                {stallCount > 0 ? ` · 卡住自动重连 ${stallCount} 次` : ''}
              </div>
            </div>
            <div className="cx-metrics">
              <div><b>{s?.stream.fps_in ?? 0}</b>帧/秒 上传 · 处理 {s?.stream.fps_done ?? 0}</div>
              <div><b>{s?.stream.latency_ms ?? '—'}</b>ms 拍摄 → 分析完</div>
              <div><b>{s?.stream.infer_ms ?? '—'}</b>ms 推理（{s?.stream.model ?? '—'}）</div>
              <div><b>{s?.stream.view_latency_ms ?? '—'}</b>ms 云台 → 画面动</div>
              <div className="cx-metrics__wide">
                取帧：{upload.source === 'tap' ? '缓冲前直取' : upload.source === 'sdk' ? 'SDK 逐帧（缓冲后）' : '等待出流'}
                {tapStats.errors ? ` · 解码错误 ${tapStats.errors}` : ''}{upload.error ? ` · ${upload.error}` : ''}
              </div>
              <div className="cx-metrics__wide">
                {v ? `${v.persons} 人 · f1 ${v.posture?.f1 ?? '—'} · 髋角 ${v.posture?.hip ?? '—'}°` : '—'}
                {th ? ` · 阈值 ${th.sit_f1.toFixed(2)} / ${th.stand_f1.toFixed(2)}${th.calibrated ? '（已标定）' : '（默认）'}` : ''}
              </div>
            </div>
          </div>

          <div className="btn-row">
            <span className="btn-row__label">会话</span>
            <button className="btn btn--small" onClick={() => run(() => api('/api/sessions', { method: 'POST', json: { mode: 'daily', label: 'alone' } }))}>
              日常 · 我一个人
            </button>
            <button className="btn btn--small" onClick={() => run(() => api('/api/sessions', { method: 'POST', json: { mode: 'daily', label: 'companion' } }))}>
              日常 · 有人陪
            </button>
            <button className="btn btn--small" onClick={() => run(() => api('/api/sessions', { method: 'POST', json: { mode: 'training', label: 'alone' } }))}>
              开始训练
            </button>
            <button className="btn btn--small btn--secondary" disabled={!live} onClick={() => run(() => api('/api/sessions/current/pause', { method: 'POST' }))}>暂停</button>
            <button className="btn btn--small btn--secondary" disabled={sess?.status !== 'paused'} onClick={() => run(() => api('/api/sessions/current/resume', { method: 'POST' }))}>继续</button>
            <button className="btn btn--small btn--secondary" disabled={!live} onClick={() => run(() => api('/api/sessions/current/stop', { method: 'POST' }))}>结束</button>
          </div>
          <div className="btn-row">
            <span className="btn-row__label">训练</span>
            <button className="btn btn--small" disabled={!training || !!stsActive || exActive}
              onClick={() => run(() => api('/api/training/sts/start', { method: 'POST' }))}>30 秒坐站</button>
            {planOn.map((x) => (
              <button key={x.kind} className="btn btn--small" disabled={!training || !!stsActive || exActive}
                onClick={() => run(() => api(`/api/training/exercise/${x.kind}/start`, { method: 'POST' }))}>{EX_NAME[x.kind]}</button>
            ))}
            {exActive && (
              <button className="btn btn--small btn--secondary" onClick={() => run(() => api('/api/training/exercise/cancel', { method: 'POST' }))}>结束{ex?.name}</button>
            )}
            {stsActive && (
              <button className="btn btn--small btn--secondary" onClick={() => run(() => api('/api/training/sts/cancel', { method: 'POST' }))}>取消坐站</button>
            )}
            <button className="btn btn--small btn--secondary" onClick={toggleVoice}>电脑念口令 {voice ? '开' : '关'}</button>
          </div>
          <div className="btn-row">
            <span className="btn-row__label">云台</span>
            {[1, 2, 3, 4].map((i) => (
              <button key={i} className="btn btn--small" onClick={() => run(() => cmd('GOTO_PRESET', { index: i }))}>
                转到 {['', '日常区', '训练位', '隐私位', '正面训练位'][i]}
              </button>
            ))}
            {(['LEFT', 'RIGHT', 'UP', 'DOWN'] as const).map((d) => (
              <button key={d} className="btn btn--small btn--secondary" onClick={() => run(() => cmd('PTZ_STEP', { dir: d }))}>
                {{ LEFT: '←', RIGHT: '→', UP: '↑', DOWN: '↓' }[d]}
              </button>
            ))}
          </div>
          <div className="btn-row">
            <span className="btn-row__label">存位</span>
            {[1, 2, 3, 4].map((i) => (
              <button key={`s${i}`} className="btn btn--small btn--secondary"
                onClick={() => run(() => cmd('SAVE_PRESET', { index: i, force: true }), `已发出：保存当前位置为预置位 ${i}`)}>
                存为 {['', '日常区', '训练位', '隐私位', '正面训练位'][i]}
              </button>
            ))}
          </div>
          <div className="btn-row">
            <span className="btn-row__label">标定</span>
            <button className="btn btn--small" disabled={sess?.status !== 'observing' || !!calActive || !!stsActive}
              onClick={() => run(() => api('/api/calibration/start', { method: 'POST' }))}>标定当前机位（坐 3 秒 → 站 3 秒）</button>
            <button className="btn btn--small btn--secondary" disabled={!calActive}
              onClick={() => run(() => api('/api/calibration/cancel', { method: 'POST' }))}>取消</button>
            <button className="btn btn--small btn--secondary" disabled={!sess || !th?.calibrated}
              onClick={() => run(() => api(`/api/calibration/${PRESET_OF_MODE[sess!.mode]}`, { method: 'DELETE' }), '已恢复默认阈值')}>恢复默认阈值</button>
          </div>
          <div className="btn-row">
            <span className="btn-row__label">设备</span>
            <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('SET_TRACKING', { on: true }))}>追踪开</button>
            <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('SET_TRACKING', { on: false }))}>追踪关</button>
            <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('QUERY_STATUS'))}>查询状态</button>
            <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('QUERY_EVENTS'))}>查询事件</button>
            {resOpts.map((o) => (
              <button key={o.value} className="btn btn--small btn--secondary" disabled={String(st.resolution) === o.value}
                onClick={() => run(() => cmd('SET_RESOLUTION', { value: o.value }), `已发出：直播画质改为 ${o.desc}（重连后生效）`)}>画质 {o.desc}</button>
            ))}
            <button className="btn btn--small btn--secondary"
              onClick={() => run(() => api('/api/config', { method: 'POST', json: { rotate: s?.rotate === 180 ? 0 : 180 } }))}>画面旋转 {s?.rotate ?? '—'}°</button>
          </div>
          <div className="btn-row">
            <span className="btn-row__label">调试</span>
            <button className="btn btn--small btn--secondary"
              onClick={() => run(() => api('/api/debug/record', { method: 'POST', json: { on: !s?.debug?.recording } }))}>
              {s?.debug?.recording ? `停止调试记录（${s.debug.lines} 行）` : '开始调试记录（只记数字）'}
            </button>
            <button className="btn btn--small btn--secondary" disabled={!s?.debug?.files}
              onClick={() => run(() => api('/api/debug/records', { method: 'DELETE' }), '调试记录已全部删除')}>删除调试记录（{s?.debug?.files ?? 0}）</button>
            <button className="btn btn--small btn--secondary" onClick={bench}>模型测速</button>
            <button className="btn btn--small btn--secondary"
              onClick={() => run(() => api('/api/config', { method: 'POST', json: { demo_fast: !s?.demo_fast } }))}>久坐演示加速 {s?.demo_fast ? '开' : '关'}</button>
          </div>
          {msg && <div className="console__msg">{msg}</div>}
          <div className="btn-row">
            <span className="btn-row__label">页面</span>
            <a className="btn btn--small btn--secondary" href="#/elder" target="_blank" rel="noreferrer">老人端</a>
            <a className="btn btn--small btn--secondary" href={`#/family?token=${familyToken}`} target="_blank" rel="noreferrer">家属端</a>
            <a className="btn btn--small btn--secondary" href={`#/therapist?token=${therapistToken}`} target="_blank" rel="noreferrer">康复师端</a>
            {onDisconnect && <button className="cx-linkbtn" onClick={onDisconnect}>断开直播</button>}
          </div>
          <ul className="console__list">
            {(b?.recent || []).slice().reverse().map((c) => (
              <li key={c.id}>
                #{c.id} {CMD_TEXT[c.type] || c.type} {c.args?.index ?? c.args?.dir ?? c.args?.value ?? (c.args?.on != null ? (c.args.on ? '开' : '关') : '')} ·{' '}
                {c.state === 'done' ? `已确认 ${Math.round(c.latency_ms || 0)} ms` : c.state === 'failed' ? `失败（${c.code}）`
                  : c.state === 'expired' ? '已作废（手机断开期间发出）' : '等待设备确认…'}
                {c.motion_ms != null ? ` · 画面 ${c.motion_ms} ms 后开始动` : ''}
              </li>
            ))}
          </ul>
        </div>
      </div>
      {manage && <div className="cx-scrim" onClick={() => setManage(false)} />}
    </div>
  );
}

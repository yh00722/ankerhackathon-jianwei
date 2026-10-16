/**
 * @name 控制台
 * @description 投影给评委看的技术面板：骨架预览、观测状态、帧率与延迟、设备状态（电量 / 在线 / 指令确认）、
 * 会话与云台控制、事件流。同时负责把帧上传给分析服务。
 */

import { useEffect, useRef, useState } from 'react';
import { api, fmtTime, LiveEvent, POSTURE_TEXT, REASON_TEXT, speak, useLive } from '../api';
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

function loadVoice(): boolean {
  try {
    return localStorage.getItem('jw.voice') !== 'off';
  } catch {
    return true;
  }
}

/** 标定、30 秒坐站的口令用语音念出来（被测的人不用盯着屏幕） */
function voiceFor(e: LiveEvent): string {
  if (e.kind === 'calib') return e.phase === 'failed' ? `标定没有完成。${e.error || ''}` : e.prompt || '';
  if (e.kind === 'sts_phase') return e.phase === 'countdown' ? '准备，三，二，一' : e.prompt || '';
  if (e.kind === 'sts_rep') return String(e.reps);
  if (e.kind === 'sts_result') return `完成，${e.reps} 次`;
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

export function ConsolePanel({ stationSn, streaming, onStall }: {
  stationSn: string; streaming: boolean; onStall?: (why: 'stall' | 'no_start') => boolean;
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
  const [msg, setMsg] = useState('');

  useEffect(() => {
    api('/api/share').then((s) => setFamilyToken(s.family_token)).catch(() => {});
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
        c.width = 480;
        c.height = Math.round((480 * bmp.height) / bmp.width);
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
          ctx.lineWidth = i === v?.target ? 3 : 2;
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

  return (
    <div className="console">
      <canvas ref={canvas} className="console__preview" />

      {v?.inverted_hint && (
        <div className="console__msg console__msg--warn">
          画面里的人是倒着的（肩在髋下方）：摄像头可能倒装了。
          <button className="btn btn--small" onClick={() => run(() => api('/api/config', { method: 'POST', json: { rotate: s?.rotate === 180 ? 0 : 180 } }))}>
            把画面转 180°
          </button>
        </div>
      )}

      <div className="console__grid">
        <div className="stat">
          <div className="stat__label">观测</div>
          <div className={`stat__value ${v?.observable ? 'ok' : 'warn'}`}>
            {!s ? '未连接服务' : v?.observable ? '看得清' : `没看到：${REASON_TEXT[v?.reason || ''] || v?.reason}`}
          </div>
          <div className="stat__sub">
            {v?.posture ? `${POSTURE_TEXT[v.posture.raw]} · f1 ${v.posture.f1 ?? '—'} · 髋角 ${v.posture.hip ?? '—'}°` : '—'}
            {v ? ` · ${v.persons} 人${v.other_near ? '（他人在旁）' : ''}` : ''}
          </div>
          {th && (
            <div className="stat__sub">
              阈值 坐 &lt; {th.sit_f1.toFixed(2)} / 站 &gt; {th.stand_f1.toFixed(2)}
              {th.calibrated ? (th.stand_hip > 0 ? ` · 站需髋角 ≥ ${Math.round(th.stand_hip)}°（已标定）` : '（已标定，髋角不参与）') : '（默认，未标定）'}
            </div>
          )}
        </div>
        <div className="stat">
          <div className="stat__label">画面</div>
          <div className="stat__value">
            上传 {upload.ready ? `${s?.stream.fps_in ?? 0} 帧/秒` : '等待出流'} · 处理 {s?.stream.fps_done ?? 0} 帧/秒
          </div>
          <div className="stat__sub">
            拍摄→分析完 {s?.stream.latency_ms ?? '—'} ms · 推理 {s?.stream.infer_ms ?? '—'} ms（{s?.stream.model ?? '—'}）· 丢帧 {upload.dropped}
            {connected ? '' : ' · 分析服务未连接'}
            {upload.error ? ` · ${upload.error}` : ''}
          </div>
          <div className="stat__sub">
            取帧 {upload.source === 'tap' ? '缓冲前直取' : upload.source === 'sdk' ? 'SDK（缓冲后）' : '—'}
            {videoBufferMs != null ? ` · 页面视频缓冲 ${(videoBufferMs / 1000).toFixed(1)} 秒${upload.source === 'tap' ? '（分析不经过）' : ''}` : ''}
            {tapStats.errors ? ` · 解码错误 ${tapStats.errors}` : ''}
          </div>
          <div className="stat__sub">
            端到端（云台指令 → 画面动）{s?.stream.view_latency_ms != null ? `${s.stream.view_latency_ms} ms` : '—'}
            {' · '}画面位移 {s?.motion?.shift ?? '—'}{s?.motion?.moving ? '（在动）' : ''}
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">设备（App SDK）</div>
          <div className={`stat__value ${b?.connected ? 'ok' : 'warn'}`}>
            {b?.connected ? `设备桥已连接 · ${st.online === false ? '摄像头离线' : '摄像头在线'}` : '手机与本机断开'}
          </div>
          <div className="stat__sub">
            电量 {st.battery ?? '—'}%{st.charging ? '（充电中）' : ''} · Wi-Fi {st.wifi_level ?? '—'} · 追踪{' '}
            {st.tracking === true ? '开' : st.tracking === false ? '关' : '—'}
            {st.resolution != null ? ` · 画质 ${resName(st.resolution)}` : ''}
            {b?.camera_moving ? ' · 镜头转动中' : ''}
          </div>
        </div>
        <div className="stat">
          <div className="stat__label">会话</div>
          <div className="stat__value">
            {sess ? `${sess.mode === 'training' ? '训练' : '日常'} · ${STATUS_TEXT[sess.status]}` : '未开启'}
          </div>
          <div className="stat__sub">
            {sess ? `${sess.label === 'alone' ? '我一个人' : '有人陪'}${sess.preset_ok === false ? ' · 未收到摄像头确认' : ''}` : '—'}
            {s?.sts ? ` · 坐站：${s.sts.prompt}（${s.sts.reps} 次${s.sts.remaining_s != null ? `，剩 ${s.sts.remaining_s} 秒` : ''}）` : ''}
          </div>
          {cal && cal.phase !== 'idle' && (
            <div className="stat__sub">
              标定（预置位 {cal.preset}）：{cal.prompt}
              {calActive ? ` · ${Math.round(cal.progress * 100)}%` : ''}
              {cal.error ? ` · ${cal.error}` : ''}
            </div>
          )}
        </div>
      </div>

      <div className="console__controls">
        <div className="btn-row">
          <button className="btn" onClick={() => run(() => api('/api/sessions', { method: 'POST', json: { mode: 'daily', label: 'alone' } }))}>
            日常 · 我一个人
          </button>
          <button className="btn" onClick={() => run(() => api('/api/sessions', { method: 'POST', json: { mode: 'daily', label: 'companion' } }))}>
            日常 · 有人陪
          </button>
          <button className="btn" onClick={() => run(() => api('/api/sessions', { method: 'POST', json: { mode: 'training', label: 'alone' } }))}>
            开始训练
          </button>
          <button className="btn" disabled={!(sess?.mode === 'training' && sess.status === 'observing')}
            onClick={() => run(() => api('/api/training/sts/start', { method: 'POST' }))}>
            30 秒坐站
          </button>
          <button className="btn btn--secondary" disabled={!live} onClick={() => run(() => api('/api/sessions/current/pause', { method: 'POST' }))}>
            暂停
          </button>
          <button className="btn btn--secondary" disabled={sess?.status !== 'paused'} onClick={() => run(() => api('/api/sessions/current/resume', { method: 'POST' }))}>
            继续
          </button>
          <button className="btn btn--secondary" disabled={!live} onClick={() => run(() => api('/api/sessions/current/stop', { method: 'POST' }))}>
            结束
          </button>
        </div>
        <div className="btn-row">
          <span className="btn-row__label">云台</span>
          {[1, 2, 3].map((i) => (
            <button key={i} className="btn btn--small" onClick={() => run(() => cmd('GOTO_PRESET', { index: i }))}>
              转到 {['', '日常区', '训练位', '隐私位'][i]}
            </button>
          ))}
          {(['LEFT', 'RIGHT', 'UP', 'DOWN'] as const).map((d) => (
            <button key={d} className="btn btn--small btn--secondary" onClick={() => run(() => cmd('PTZ_STEP', { dir: d }))}>
              {{ LEFT: '←', RIGHT: '→', UP: '↑', DOWN: '↓' }[d]}
            </button>
          ))}
          {[1, 2, 3].map((i) => (
            <button key={`s${i}`} className="btn btn--small btn--secondary"
              onClick={() => run(() => cmd('SAVE_PRESET', { index: i, force: true }), `已发出：保存当前位置为预置位 ${i}`)}>
              存为 {['', '日常区', '训练位', '隐私位'][i]}
            </button>
          ))}
        </div>
        <div className="btn-row">
          <span className="btn-row__label">标定</span>
          <button className="btn btn--small" disabled={sess?.status !== 'observing' || !!calActive || !!stsActive}
            onClick={() => run(() => api('/api/calibration/start', { method: 'POST' }))}>
            标定当前机位（坐 3 秒 → 站 3 秒）
          </button>
          <button className="btn btn--small btn--secondary" disabled={!calActive}
            onClick={() => run(() => api('/api/calibration/cancel', { method: 'POST' }))}>
            取消
          </button>
          <button className="btn btn--small btn--secondary" disabled={!sess || !th?.calibrated}
            onClick={() => run(() => api(`/api/calibration/${PRESET_OF_MODE[sess!.mode]}`, { method: 'DELETE' }), '已恢复默认阈值')}>
            恢复默认阈值
          </button>
          <button className="btn btn--small btn--secondary" onClick={toggleVoice}>语音提示 {voice ? '开' : '关'}</button>
        </div>
        <div className="btn-row">
          <span className="btn-row__label">设备</span>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('SET_TRACKING', { on: true }))}>追踪开</button>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('SET_TRACKING', { on: false }))}>追踪关</button>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('QUERY_STATUS'))}>查询状态</button>
          {resOpts.map((o) => (
            <button key={o.value} className="btn btn--small btn--secondary" disabled={String(st.resolution) === o.value}
              onClick={() => run(() => cmd('SET_RESOLUTION', { value: o.value }), `已发出：直播画质改为 ${o.desc}（重连后生效）`)}>
              画质 {o.desc}
            </button>
          ))}
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('QUERY_EVENTS'))}>查询事件</button>
          <button className="btn btn--small btn--secondary"
            onClick={() => run(() => api('/api/config', { method: 'POST', json: { rotate: s?.rotate === 180 ? 0 : 180 } }))}>
            画面旋转 {s?.rotate ?? '—'}°
          </button>
          <a className="btn btn--small btn--secondary" href="#/elder" target="_blank" rel="noreferrer">老人端</a>
          <a className="btn btn--small btn--secondary" href={`#/family?token=${familyToken}`} target="_blank" rel="noreferrer">家属端</a>
        </div>
        <div className="btn-row">
          <span className="btn-row__label">调试</span>
          <button className="btn btn--small btn--secondary"
            onClick={() => run(() => api('/api/debug/record', { method: 'POST', json: { on: !s?.debug?.recording } }))}>
            {s?.debug?.recording ? `停止调试记录（${s.debug.lines} 行）` : '开始调试记录（只记数字）'}
          </button>
          <button className="btn btn--small btn--secondary" disabled={!s?.debug?.files}
            onClick={() => run(() => api('/api/debug/records', { method: 'DELETE' }), '调试记录已全部删除')}>
            删除调试记录（{s?.debug?.files ?? 0} 个文件）
          </button>
          <button className="btn btn--small btn--secondary" onClick={bench}>模型测速</button>
        </div>
        {msg && <div className="console__msg">{msg}</div>}
      </div>

      <div className="console__cols">
        <div>
          <div className="console__title">事件</div>
          <ul className="console__list">
            {(s?.events || []).map((e, i) => (
              <li key={i}>
                <span className="t">{fmtTime(e.at)}</span> {e.text}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="console__title">设备指令与事件（App SDK）</div>
          <ul className="console__list">
            {(b?.recent || []).slice().reverse().map((c) => (
              <li key={c.id}>
                #{c.id} {CMD_TEXT[c.type] || c.type} {c.args?.index ?? c.args?.dir ?? c.args?.value ?? (c.args?.on != null ? (c.args.on ? '开' : '关') : '')} ·{' '}
                {c.state === 'done' ? `已确认 ${Math.round(c.latency_ms || 0)} ms` : c.state === 'failed' ? `失败（${c.code}）`
                  : c.state === 'expired' ? '已作废（手机断开期间发出，没有执行）' : '等待设备确认…'}
                {c.motion_ms != null ? ` · 画面 ${c.motion_ms} ms 后开始动` : ''}
              </li>
            ))}
            {(b?.events || []).slice(0, 5).map((e, i) => (
              <li key={`e${i}`} className="muted">
                设备事件 {e.start_time || ''}（类型 {e.type || '—'}）
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

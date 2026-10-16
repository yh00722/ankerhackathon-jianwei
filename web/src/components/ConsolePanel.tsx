/**
 * @name 控制台
 * @description 投影给评委看的技术面板：骨架预览、观测状态、帧率与延迟、设备状态（电量 / 在线 / 指令确认）、
 * 会话与云台控制、事件流。同时负责把帧上传给分析服务。
 */

import { useEffect, useRef, useState } from 'react';
import { api, fmtTime, POSTURE_TEXT, REASON_TEXT, useLive } from '../api';
import { useFrameUploader } from './useFrameUploader';

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
  QUERY_STATUS: '查询状态',
  QUERY_EVENTS: '查询事件',
};

function cmd(type: string, args: Record<string, any> = {}) {
  return api('/api/device/command', { method: 'POST', json: { type, args } });
}

export function ConsolePanel({ stationSn, streaming }: { stationSn: string; streaming: boolean }) {
  const { state, connected } = useLive();
  const lastBlob = useRef<Blob | null>(null);
  const upload = useFrameUploader(stationSn, streaming, (b) => (lastBlob.current = b));
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

  return (
    <div className="console">
      <canvas ref={canvas} className="console__preview" />

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
        </div>
        <div className="stat">
          <div className="stat__label">画面</div>
          <div className="stat__value">
            上传 {upload.ready ? `${s?.stream.fps_in ?? 0} 帧/秒` : '等待出流'} · 处理 {s?.stream.fps_done ?? 0} 帧/秒
          </div>
          <div className="stat__sub">
            延迟 {s?.stream.latency_ms ?? '—'} ms · 推理 {s?.stream.infer_ms ?? '—'} ms · 丢帧 {upload.dropped}
            {connected ? '' : ' · 分析服务未连接'}
            {upload.error ? ` · ${upload.error}` : ''}
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
          <span className="btn-row__label">设备</span>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('SET_TRACKING', { on: true }))}>追踪开</button>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('SET_TRACKING', { on: false }))}>追踪关</button>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('QUERY_STATUS'))}>查询状态</button>
          <button className="btn btn--small btn--secondary" onClick={() => run(() => cmd('QUERY_EVENTS'))}>查询事件</button>
          <button className="btn btn--small btn--secondary"
            onClick={() => run(() => api('/api/config', { method: 'POST', json: { rotate: s?.rotate === 180 ? 0 : 180 } }))}>
            画面旋转 {s?.rotate ?? '—'}°
          </button>
          <a className="btn btn--small btn--secondary" href="#/elder" target="_blank" rel="noreferrer">老人端</a>
          <a className="btn btn--small btn--secondary" href={`#/family?token=${familyToken}`} target="_blank" rel="noreferrer">家属端</a>
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
                #{c.id} {CMD_TEXT[c.type] || c.type} {c.args?.index ?? c.args?.dir ?? (c.args?.on != null ? (c.args.on ? '开' : '关') : '')} ·{' '}
                {c.state === 'done' ? `已确认 ${Math.round(c.latency_ms || 0)} ms` : c.state === 'failed' ? `失败（${c.code}）` : '等待设备确认…'}
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

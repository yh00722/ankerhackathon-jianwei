/**
 * @name AI 看到的画面（老人手机，折叠屏展开时）
 * @description 从分析服务的 /ws/preview 收最新一帧（经 USB 到手机，只在内存里），叠上骨架和当前判断。
 * 和控制台大屏看到的是同一个画面；手机折叠时不挂载，不传画面。
 */

import { useEffect, useRef, useState } from 'react';
import { fmtTime, LiveState, POSTURE_TEXT, REASON_TEXT } from '../api';
import { drawFrame } from '../utils/skeleton';

const ICON: Record<string, string> = { session: '📷', sts_result: '🪑', ex_result: '🏋️', calib: '📐', reminder: '⏰', reminder_end: '🚶' };

function icon(e: any): string {
  if (e.kind === 'rise') return e.complete ? '✅' : '⚠️';
  if (e.kind === 'ex_rep') return e.counted ? '✔️' : '⚠️';
  return ICON[e.kind] || 'ℹ️';
}

export function AiView({ state }: { state: LiveState | null }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const frame = useRef<Blob | null>(null);
  const lastAt = useRef(0);
  const [live, setLive] = useState(false);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: number | undefined;
    const open = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws/preview`);
      ws.binaryType = 'blob';
      ws.onmessage = (m) => {
        frame.current = m.data as Blob;
        lastAt.current = Date.now();
      };
      ws.onclose = () => {
        if (!closed) timer = window.setTimeout(open, 1000);
      };
    };
    open();
    let busy = false;
    const iv = window.setInterval(async () => {
      setLive(Date.now() - lastAt.current < 3000);
      const c = canvas.current;
      const b = frame.current;
      if (!c || !b || busy) return;
      frame.current = null;
      busy = true;
      try {
        await drawFrame(c, b, stateRef.current?.view, stateRef.current?.rotate ?? 0, 720);
      } catch {
        /* 坏帧跳过 */
      } finally {
        busy = false;
      }
    }, 125);
    return () => {
      closed = true;
      window.clearTimeout(timer);
      window.clearInterval(iv);
      ws?.close();
    };
  }, []);

  const s = state;
  const v = s?.view;
  const ex = s?.exercise;
  const sts = s?.sts;
  const stateText = !v ? '—' : !v.observable ? `没看到：${REASON_TEXT[v.reason || ''] || v.reason || '—'}`
    : `${POSTURE_TEXT[v.posture?.raw || 'unknown']}${v.other_near ? ' · 旁边有人' : ''}`;
  const prompt = ex && !['done', 'aborted', 'idle'].includes(ex.phase)
    ? `${ex.name}${ex.reps != null && ex.phase === 'running' ? ` · ${ex.reps}${ex.target_reps ? ` / ${ex.target_reps}` : ''} 次` : ''}${ex.holding_s != null ? ` · 保持 ${ex.holding_s} 秒` : ''}`
    : sts && !['done', 'aborted'].includes(sts.phase) ? `30 秒坐站${sts.phase === 'running' ? ` · ${sts.reps} 次 · 剩 ${Math.ceil(sts.remaining_s ?? 0)} 秒` : ''}` : '';

  return (
    <section className="ai-view" aria-label="AI 看到的画面">
      <div className="ai-view__title">AI 看到的画面（实时）</div>
      <div className="ai-view__frame">
        <canvas ref={canvas} />
        {!live && <div className="ai-view__wait">等待画面…</div>}
        {prompt && <div className="ai-view__prompt">{prompt}</div>}
        <div className={`ai-view__state ${v?.observable ? 'ok' : 'warn'}`}>{stateText}</div>
        {s?.bridge?.camera_moving && <div className="ai-view__moving">镜头转动中</div>}
      </div>
      <div className="e-note">🔒 画面只在电脑和您的手机上显示，不保存；记录里只有次数和时长。</div>
      {(s?.events || []).length > 0 && (
        <div className="ai-view__log">
          <div className="ai-view__log-title">刚才识别到的</div>
          {(s?.events || []).slice(0, 4).map((e, i) => (
            <div className="ai-view__ev" key={i}>
              <span aria-hidden>{icon(e)}</span>
              <span className="ai-view__t">{fmtTime(e.at)}</span>
              <span>{e.text}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

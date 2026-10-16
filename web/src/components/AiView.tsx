/**
 * @name AI 看到的画面（老人手机，折叠屏展开时）
 * @description 从分析服务的 /ws/preview 收最新一帧（经 USB 到手机，只在内存里），叠上骨架和当前判断。
 * 和控制台大屏看到的是同一个画面；手机折叠时不挂载，不传画面。
 * 点一下画面放大到全屏（演示时给人看），再点一下或按 Esc 缩回。
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
  const [big, setBig] = useState(false);
  const bigRef = useRef(big);
  bigRef.current = big;

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
      if (document.hidden) return; // 页签隐藏时不解码、不画（App 里切到别的页签）
      const c = canvas.current;
      const b = frame.current;
      if (!c || !b || busy) return;
      frame.current = null;
      busy = true;
      try {
        await drawFrame(c, b, stateRef.current?.view, stateRef.current?.rotate ?? 0, bigRef.current ? 960 : 720);
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

  useEffect(() => {
    if (!big) return;
    const f = (e: KeyboardEvent) => e.key === 'Escape' && setBig(false);
    window.addEventListener('keydown', f);
    return () => window.removeEventListener('keydown', f);
  }, [big]);

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
    <section className={big ? 'ai-view ai-view--big' : 'ai-view'} aria-label="AI 看到的画面">
      <div className="ai-view__title">AI 看到的画面（实时）</div>
      <div className="ai-view__frame" role="button" tabIndex={0} aria-label={big ? '缩小画面' : '放大画面'}
        onClick={() => setBig(!big)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setBig(!big);
          }
        }}>
        <canvas ref={canvas} />
        {!live && <div className="ai-view__wait">等待画面…</div>}
        {prompt && <div className="ai-view__prompt">{prompt}</div>}
        <div className={`ai-view__state ${v?.observable ? 'ok' : 'warn'}`}>{stateText}</div>
        {s?.bridge?.camera_moving && <div className="ai-view__moving">镜头转动中</div>}
        <div className="ai-view__zoom" aria-hidden>{big ? '✕ 缩小' : '⤢ 放大'}</div>
      </div>
      <div className="e-note">🔒 画面不保存，只记次数和时长。</div>
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

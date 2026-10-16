/**
 * @name 分析服务客户端
 * @description 访问 server/ 的 REST 接口和 /ws/live 实时推送。路径都是相对的，由 vite 代理到 127.0.0.1:8000。
 */

import { useEffect, useRef, useState } from 'react';

export interface Session {
  id: number;
  mode: 'daily' | 'training';
  label: 'alone' | 'companion';
  status: 'starting' | 'observing' | 'paused' | 'ended';
  cmd_id: number;
  started_at: number;
  preset_ok: boolean | null;
}

export interface LiveEvent {
  kind: string;
  text?: string;
  at: number;
  id?: number;
  complete?: boolean;
  reason?: string | null;
  other_person?: boolean;
  duration_s?: number | null;
  phase?: string;
  prompt?: string;
  reps?: number;
  valid?: boolean;
  error?: string;
  exercise?: string;
  name?: string;
  counted?: boolean;
  side?: string;
  hold_s?: number;
  ok?: boolean;
  ask_support?: boolean;
  n?: number;
  ended_by?: string;
  by?: string;
  again?: boolean;
  arms?: boolean;
}

/** 训练动作的实时状态（服务端 exercises.py 的 status()） */
export interface ExerciseStatus {
  exercise: string;
  name: string;
  phase: string;
  prompt: string;
  invalid: string[];
  elapsed_s?: number;
  reps?: number;
  target_reps?: number;
  hold_target_s?: number;
  by_side?: Record<string, number>;
  holding_s?: number | null;
  variant?: string;
  variant_text?: string;
  target_s?: number;
  attempts?: number;
  max_attempts?: number;
  best_s?: number | null;
  remaining_s?: number;
  duration_s?: number;
}

export interface PlanItem {
  kind: string;
  enabled: boolean;
  params: Record<string, any>;
  updated_at: number;
}

export const EX_NAME: Record<string, string> = {
  sts30: '30 秒坐站',
  knee_ext: '坐位伸膝',
  knee_flex: '坐位屈膝',
  balance: '站立平衡',
  march2min: '2 分钟原地抬膝',
  weight_shift: '扶台面转移重心',
};

export const VARIANT_TEXT: Record<string, string> = {
  together: '并脚站',
  semi_tandem: '半前后脚站',
  tandem: '前后脚站',
  single: '单脚站',
};

const SIDE_TEXT: Record<string, string> = { both: '两腿', left: '左腿', right: '右腿' };

/** 计划里一项的简短说明，例如「坐位伸膝 · 10 次 · 每次保持 3 秒 · 两腿」 */
export function planLabel(p: PlanItem): string {
  const x = p.params || {};
  const parts = [EX_NAME[p.kind] || p.kind];
  if (x.target_reps) parts.push(`${x.target_reps} 次`);
  if (x.hold_s) parts.push(`每次保持 ${x.hold_s} 秒`);
  if (x.variant) parts.push(VARIANT_TEXT[x.variant] || x.variant);
  if (x.target_s) parts.push(`目标 ${x.target_s} 秒`);
  if (x.duration_s && p.kind !== 'weight_shift') parts.push(`${Math.round(x.duration_s / 60) || 1} 分钟`);
  if (x.side) parts.push(SIDE_TEXT[x.side] || x.side);
  return parts.join(' · ');
}

/** 训练动作是否还在进行（准备、计时、两次之间） */
export function exerciseActive(e: ExerciseStatus | null | undefined): boolean {
  return !!e && !['done', 'aborted', 'idle'].includes(e.phase);
}

export interface CalibStatus {
  phase: 'sit' | 'stand' | 'done' | 'failed' | 'aborted' | 'idle';
  prompt: string;
  preset: number | null;
  progress: number;
  error?: string;
  result?: { sit_f1: number; stand_f1: number; sit_hip: number | null; stand_hip: number | null };
}

export interface LiveState {
  server_time: number;
  session: Session | null;
  view: {
    observable: boolean;
    reason: string | null;
    posture: {
      raw: string; f1: number | null; hip: number | null; knee: number | null; sit_stable?: boolean; stand_stable?: boolean;
    } | null;
    persons: number;
    other_near: boolean;
    target?: number | null;
    inverted_hint?: boolean;
    skeletons?: number[][][];
  };
  stream: {
    fps_in: number; fps_done: number; latency_ms: number | null; infer_ms: number | null; frame_age_s: number | null;
    view_latency_ms: number | null; source?: string | null; model?: string;
  };
  bridge: {
    connected: boolean;
    status: Record<string, any>;
    status_age_s: number | null;
    camera_moving: boolean;
    recent: {
      id: number; type: string; args: any; state: string; ok: boolean | null; code: number | null; latency_ms: number | null;
      motion_ms?: number | null;
    }[];
    events: { id?: string; start_time?: string; type?: string }[];
  };
  motion: { shift: number | null; response: number | null; moving: boolean };
  calib: CalibStatus | null;
  thresholds: { calibrated: boolean; sit_f1: number; stand_f1: number; sit_hip: number; stand_hip: number };
  debug: { recording: boolean; file: string | null; lines: number; files: number };
  exercise: ExerciseStatus | null;
  last_exercise: (LiveEvent & Record<string, any>) | null;
  plan: PlanItem[];
  reminder: { id: number; times: number; at: number; response?: string; snooze_until: number | null } | null;
  demo_fast: boolean;
  sts: { phase: string; prompt: string; reps: number; remaining_s?: number } | null;
  last_sts: { reps: number; valid: boolean; invalid_reasons: string[]; at: number } | null;
  current_sit_s: number | null;
  events: LiveEvent[];
  rotate: number;
}

export const REASON_TEXT: Record<string, string> = {
  STREAM_DOWN: '摄像头暂时断开',
  CAMERA_MOVING: '摄像头在转动',
  NO_PERSON: '画面里没有人',
  AMBIGUOUS: '旁边有人，分不清',
  LOW_QUALITY: '看不清',
  INVERTED: '画面是倒的',
  GAP: '中间没看到',
};

export const POSTURE_TEXT: Record<string, string> = {
  sit: '坐着',
  stand: '站着',
  transition: '正在起身 / 坐下',
  unknown: '不确定',
};

export async function api<T = any>(path: string, init?: RequestInit & { json?: any }): Promise<T> {
  const { json, ...rest } = init || {};
  const res = await fetch(path, {
    ...rest,
    headers: { ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(rest.headers || {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err: any = new Error((data && (data.detail?.reason || data.detail)) || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data as T;
}

/** 订阅 /ws/live：返回最新状态和最近的实时事件；断开后自动重连。 */
export function useLive(onEvent?: (e: LiveEvent) => void) {
  const [state, setState] = useState<LiveState | null>(null);
  const [connected, setConnected] = useState(false);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: number | undefined;
    const open = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws/live`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.type === 'state') setState(msg.state);
        else if (msg.type === 'event') cb.current?.(msg.event);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(open, 1000);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { state, connected };
}

export function fmtDur(s: number | null | undefined): string {
  if (s == null) return '—';
  const v = Math.round(s);
  if (v < 60) return `${v} 秒`;
  const m = Math.floor(v / 60);
  if (m < 60) return `${m} 分钟`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

/** 带日期的时间（北京时间），例如「10/15 23:08」 */
export function fmtDateTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function fmtTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** 中文语音提示（标定、30 秒坐站的口令）。浏览器不支持时静默。 */
export function speak(text: string) {
  try {
    if (!text || !('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'zh-CN';
    u.rate = 0.95;
    window.speechSynthesis.speak(u);
  } catch {
    /* 没有语音也不影响使用 */
  }
}

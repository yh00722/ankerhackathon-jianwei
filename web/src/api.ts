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
}

export interface LiveState {
  server_time: number;
  session: Session | null;
  view: {
    observable: boolean;
    reason: string | null;
    posture: { raw: string; f1: number | null; hip: number | null; knee: number | null } | null;
    persons: number;
    other_near: boolean;
    target?: number | null;
    skeletons?: number[][][];
  };
  stream: { fps_in: number; fps_done: number; latency_ms: number | null; infer_ms: number | null; frame_age_s: number | null };
  bridge: {
    connected: boolean;
    status: Record<string, any>;
    status_age_s: number | null;
    camera_moving: boolean;
    recent: { id: number; type: string; args: any; state: string; ok: boolean | null; code: number | null; latency_ms: number | null }[];
    events: { id?: string; start_time?: string; type?: string }[];
  };
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

export function fmtTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

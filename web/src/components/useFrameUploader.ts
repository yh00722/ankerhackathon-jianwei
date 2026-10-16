/**
 * @name 帧上传
 * @description 用 Web SDK 的 subscribeMediaChannelFrames 逐帧取 JPEG，经 /ws/frames 发给本机分析服务。
 * 画面不落盘：只在浏览器内存里转一手。
 *
 * 帧格式：4 字节大端头长度 + 头 JSON（ts 微秒、cap 采集时刻毫秒）+ JPEG。
 * 背压：WebSocket 缓冲超过 1 MB 就丢帧，只保留最新的，保证实时。
 */

import { useEffect, useRef, useState } from 'react';

export interface UploadStats {
  ws: boolean;
  ready: boolean;
  sent: number;
  dropped: number;
  error: string;
}

const PERMANENT = /仅支持|无法获取|不支持/;

export function useFrameUploader(stationSn: string, active: boolean, onFrame?: (blob: Blob) => void): UploadStats {
  const [stats, setStats] = useState<UploadStats>({ ws: false, ready: false, sent: 0, dropped: 0, error: '' });
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;

  useEffect(() => {
    if (!active) return;
    let ws: WebSocket | null = null;
    let unsub: (() => void) | null = null;
    let stopped = false;
    let probe: number | undefined;
    let sent = 0;
    let dropped = 0;
    let lastFrameAt = 0;
    let subscribedAt = 0;
    const enc = new TextEncoder();

    const openWs = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws/frames`);
      ws.binaryType = 'arraybuffer';
      ws.onclose = () => {
        if (!stopped) window.setTimeout(openWs, 1000);
      };
    };
    openWs();

    const subscribe = () => {
      try {
        unsub = window.WebSDK.subscribeMediaChannelFrames(
          stationSn,
          async (f: any) => {
            lastFrameAt = Date.now();
            onFrameRef.current?.(f.blob);
            if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > 1_000_000) {
              dropped++;
              return;
            }
            const head = enc.encode(JSON.stringify({ ts: f.timestamp, cap: f.capturedAt }));
            const body = new Uint8Array(await f.blob.arrayBuffer());
            const buf = new Uint8Array(4 + head.length + body.length);
            new DataView(buf.buffer).setUint32(0, head.length);
            buf.set(head, 4);
            buf.set(body, 4 + head.length);
            ws.send(buf);
            sent++;
          },
          // 不在浏览器端限流：设定间隔接近摄像头帧周期时会隔帧跳过；处理不过来由服务端丢帧
          { intervalMs: 0, quality: 0.7 },
        );
        subscribedAt = Date.now();
        setStats((s) => ({ ...s, ready: true, error: '' }));
      } catch (e: any) {
        const msg = String(e?.message || e);
        if (PERMANENT.test(msg)) {
          setStats((s) => ({ ...s, error: msg }));
          return;
        }
        // 流还没建立（Transport not found），稍后重试
        probe = window.setTimeout(subscribe, 500);
      }
    };
    subscribe();

    // 看门狗：直播被设备挂断并自动重连后，旧订阅会挂在失效的连接上、不再回调。
    // 3 秒收不到帧就重新订阅。
    const iv = window.setInterval(() => {
      const now = Date.now();
      if (unsub && now - Math.max(lastFrameAt, subscribedAt) > 3000) {
        unsub();
        unsub = null;
        setStats((s) => ({ ...s, ready: false }));
        subscribe();
      }
      setStats((s) => ({ ...s, ws: ws?.readyState === WebSocket.OPEN, sent, dropped }));
    }, 1000);

    return () => {
      stopped = true;
      window.clearTimeout(probe);
      window.clearInterval(iv);
      unsub?.();
      ws?.close();
    };
  }, [stationSn, active]);

  return stats;
}

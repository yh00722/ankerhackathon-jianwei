/**
 * @name 帧上传
 * @description 取帧经 /ws/frames 发给本机分析服务。画面不落盘：只在浏览器内存里转一手。
 *
 * 帧来源（优先级从高到低）：
 * 1. 抖动缓冲之前截取的编码帧自己解码（utils/low-latency.ts）：比页面上播放的视频早 3–6 秒；
 *    在浏览器里先缩到 960 宽再压 JPEG（服务端本来也缩到 960），上传量约为原来的 1/3。
 * 2. 截取不可用或 1.5 秒没出帧：退回 SDK 的 subscribeMediaChannelFrames（缓冲之后的视频轨）。
 *    直取稳定 3 秒后暂停 SDK 取帧（它每帧都压一张 1080P JPEG，白占 CPU），直取一断就恢复。
 *
 * 帧格式：4 字节大端头长度 + 头 JSON（cap 拍摄时刻毫秒：直取帧按 RTP 还原，SDK 帧是取帧时刻；src 来源）+ JPEG。
 * 成批到达的帧依次压缩发送（压一帧约 4 毫秒），服务端处理不过来时只处理最新的。
 * 背压：WebSocket 缓冲超过 1 MB 就丢帧，只保留最新的，保证实时。
 * 卡住检测：出过帧后 10 秒没新帧（重新订阅也没救回来），或挂载 180 秒还没出帧，调用 onStall，
 * 由上层决定要不要整个重连（返回 true 表示接受；不接受会每秒再问一次）。
 */

import { useEffect, useRef, useState } from 'react';
import { onTapFrame, tapHealthy, tapStats } from '../utils/low-latency';

export interface UploadStats {
  ws: boolean;
  ready: boolean;
  sent: number;
  dropped: number;
  error: string;
  source: 'tap' | 'sdk' | '';
}

const PERMANENT = /仅支持|无法获取|不支持/;
const STALL_MS = 10000;
const NO_START_MS = 180000; // 今晚实测建连要 60–110 秒（信令慢），太早重连会把正在建立的连接打断
const TAP_WIDTH = 960;

export function useFrameUploader(
  stationSn: string,
  active: boolean,
  onFrame?: (blob: Blob) => void,
  onStall?: (why: 'stall' | 'no_start') => boolean,
): UploadStats {
  const [stats, setStats] = useState<UploadStats>({ ws: false, ready: false, sent: 0, dropped: 0, error: '', source: '' });
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const onStallRef = useRef(onStall);
  onStallRef.current = onStall;

  useEffect(() => {
    if (!active) return;
    let ws: WebSocket | null = null;
    let unsub: (() => void) | null = null;
    let stopped = false;
    let probe: number | undefined;
    let sent = 0;
    let dropped = 0;
    let lastFrameAt = 0;
    let lastSdkAt = 0;
    let subscribedAt = 0;
    let stallReported = false;
    let encoding = false;
    let sdkPaused = false;
    let tapGoodSince = 0;
    let source: UploadStats['source'] = '';
    const mountedAt = Date.now();
    const enc = new TextEncoder();
    const canvas = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(TAP_WIDTH, 540) : null;
    const ctx = canvas?.getContext('2d', { alpha: false }) as OffscreenCanvasRenderingContext2D | null;

    const openWs = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws/frames`);
      ws.binaryType = 'arraybuffer';
      ws.onclose = () => {
        if (!stopped) window.setTimeout(openWs, 1000);
      };
    };
    openWs();

    const wsReady = () => !!ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount <= 1_000_000;

    const send = async (blob: Blob, capMs: number, src: 'tap' | 'sdk') => {
      lastFrameAt = Date.now();
      source = src;
      onFrameRef.current?.(blob);
      if (!wsReady()) {
        dropped++;
        return;
      }
      const head = enc.encode(JSON.stringify({ cap: capMs, src }));
      const body = new Uint8Array(await blob.arrayBuffer());
      const buf = new Uint8Array(4 + head.length + body.length);
      new DataView(buf.buffer).setUint32(0, head.length);
      buf.set(head, 4);
      buf.set(body, 4 + head.length);
      ws!.send(buf);
      sent++;
    };

    // 来源 1：抖动缓冲之前的帧。排队依次压缩（最多攒 3 帧，解码器的输出缓冲有限，不能久占）
    const queue: { vf: VideoFrame; cap: number }[] = [];
    const pump = async () => {
      if (encoding || !canvas || !ctx) return;
      encoding = true;
      try {
        while (queue.length && !stopped) {
          const { vf, cap } = queue.shift()!;
          try {
            const h = Math.round((TAP_WIDTH * vf.displayHeight) / vf.displayWidth);
            if (canvas.height !== h) canvas.height = h;
            ctx.drawImage(vf, 0, 0, TAP_WIDTH, h);
            vf.close();
            const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.75 });
            await send(blob, cap, 'tap');
          } catch {
            vf.close();
          }
        }
      } finally {
        encoding = false;
      }
    };
    if (canvas && ctx) {
      onTapFrame((vf, capMs) => {
        if (stopped || !wsReady()) {
          vf.close();
          dropped++;
          return;
        }
        queue.push({ vf, cap: capMs });
        while (queue.length > 3) {
          queue.shift()!.vf.close();
          dropped++;
        }
        pump();
      });
    }

    // 来源 2：SDK 取帧。截取正常时不处理它的帧（也就不再压缩 1080P）
    const subscribe = () => {
      if (sdkPaused || stopped) return;
      try {
        unsub = window.WebSDK.subscribeMediaChannelFrames(
          stationSn,
          async (f: any) => {
            lastSdkAt = Date.now();
            if (tapHealthy()) return;
            await send(f.blob, f.capturedAt, 'sdk');
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
    // 3 秒收不到 SDK 的帧就重新订阅。
    const iv = window.setInterval(() => {
      const now = Date.now();
      const tapOk = tapHealthy();
      tapGoodSince = tapOk ? tapGoodSince || now : 0;
      if (tapOk && !sdkPaused && now - tapGoodSince > 3000) {
        sdkPaused = true;
        window.clearTimeout(probe);
        unsub?.();
        unsub = null;
      } else if (!tapOk && sdkPaused) {
        sdkPaused = false;
        subscribe();
      } else if (unsub && now - Math.max(lastSdkAt, subscribedAt) > 3000) {
        unsub();
        unsub = null;
        setStats((s) => ({ ...s, ready: false }));
        subscribe();
      }
      if (!stallReported && onStallRef.current) {
        const why = lastFrameAt && now - lastFrameAt > STALL_MS ? 'stall' : !lastFrameAt && now - mountedAt > NO_START_MS ? 'no_start' : null;
        if (why) stallReported = onStallRef.current(why);
      }
      setStats((s) => ({ ...s, ws: ws?.readyState === WebSocket.OPEN, sent, dropped, source, ready: s.ready || tapHealthy() }));
    }, 1000);

    return () => {
      stopped = true;
      onTapFrame(null);
      queue.splice(0).forEach((q) => q.vf.close());
      window.clearTimeout(probe);
      window.clearInterval(iv);
      unsub?.();
      ws?.close();
    };
  }, [stationSn, active]);

  return stats;
}

export { tapStats };

/**
 * @name 低延迟取帧
 * @description 实测（10/16 晚，requestVideoFrameCallback 的 presentationTime − receiveTime）：
 * 摄像头的帧到了浏览器以后，要在 WebRTC 抖动缓冲里等 3–6 秒才显示出来；SDK 的 subscribeMediaChannelFrames
 * 从视频轨取帧（MediaStreamTrackProcessor），也在缓冲之后，所以分析看到的画面比真实动作晚好几秒。
 * jitterBufferTarget 只能设缓冲的下限，压不下来。
 *
 * 原因（同晚实测 40 秒 579 帧）：摄像头的 RTP 时钟比电脑快约 0.94%，抖动缓冲以为帧越来越「晚到」，
 * 每分钟多攒约 0.56 秒，攒到上限再复位，所以延迟在 3–6 秒之间来回。
 *
 * 做法：创建连接时打开 encodedInsertableStreams，在抖动缓冲之前拿到组好的 H.264 编码帧，
 * 原样放回去（页面上的视频照常播放），同时复制一份用 WebCodecs 自己解码，解出的帧交给帧上传。
 * 帧是成批到达的（间隔 p10 = 0、p99 ≈ 0.44 秒），所以每帧的拍摄时刻用 RTP 时间戳还原：
 * 拍摄时刻 ≈ RTP 媒体时间 + 最近 5 秒里「到达时刻 − 媒体时间」的最小值（最早到的那帧几乎没排队），
 * 5 秒窗口内时钟漂移不到 50 毫秒。
 * - 网址带 ?tap=off：不截取，退回 SDK 取帧（用于对比或排查）
 * - window.__jw_tap：截取与解码的计数；window.__jw_pcs：最近的连接（调试用 getStats）
 * - 编码帧只在内存里转一手，不落盘
 */

type TapListener = (frame: VideoFrame, captureWallMs: number) => void;

export const tapStats = {
  enabled: false,
  encoded: 0,
  decoded: 0,
  errors: 0,
  lastDecodedAt: 0,
  codec: '',
  lastError: '',
  /** 最近 600 帧的（收到时刻 ms，RTP 时间戳）：调试用，看到达节奏和摄像头时钟 */
  ring: [] as [number, number][],
};

let listener: TapListener | null = null;

/** 帧上传订阅解码后的帧（同一时间只有一个订阅者）。回调负责 close() 帧。 */
export function onTapFrame(fn: TapListener | null) {
  listener = fn;
}

export function tapHealthy(withinMs = 1500): boolean {
  return tapStats.enabled && Date.now() - tapStats.lastDecodedAt < withinMs;
}

const piped = new WeakSet<object>();

/** 不需要截取的发送端 / 接收端（音频、对讲）：编码帧原样放行。开了 encodedInsertableStreams 就必须每个都接上。 */
function passThrough(x: any) {
  if (!x || piped.has(x) || typeof x.createEncodedStreams !== 'function') return;
  piped.add(x);
  try {
    const { readable, writable } = x.createEncodedStreams();
    readable.pipeTo(writable).catch(() => {});
  } catch {
    /* 已经被接过 */
  }
}

function codecOf(receiver: RTCRtpReceiver): string {
  const c = (receiver.getParameters?.().codecs || []).find((x) => /h264/i.test(x.mimeType));
  const pl = c?.sdpFmtpLine?.match(/profile-level-id=([0-9a-f]{6})/i)?.[1];
  return `avc1.${(pl || '42e01f').toLowerCase()}`;
}

function tapVideo(receiver: RTCRtpReceiver) {
  if (piped.has(receiver)) return;
  piped.add(receiver);
  const { readable, writable } = (receiver as any).createEncodedStreams();
  let dec: VideoDecoder | null = null;
  let needKey = true;
  const capAt = new Map<number, number>(); // chunk 时间戳 → 估计的拍摄时刻（墙钟毫秒）
  const offs: [number, number][] = []; // 最近 5 秒的（到达时刻, 到达时刻 − 媒体时间）
  let rtpBase = 0;
  let lastRtp = -1;
  const captureTime = (rtp: number, recv: number) => {
    if (lastRtp >= 0 && rtp < lastRtp - 2 ** 31) rtpBase += 2 ** 32; // RTP 时间戳 32 位回绕
    lastRtp = rtp;
    const media = (rtpBase + rtp) / 90; // H.264 视频 RTP 时钟 90 kHz
    offs.push([recv, recv - media]);
    while (offs.length && offs[0][0] < recv - 5000) offs.shift();
    let m = Infinity;
    for (const [, o] of offs) m = Math.min(m, o);
    return media + m;
  };

  const decoder = () => {
    if (dec && dec.state !== 'closed') return dec;
    tapStats.codec = codecOf(receiver);
    dec = new VideoDecoder({
      output: (f) => {
        tapStats.decoded++;
        tapStats.lastDecodedAt = Date.now();
        const wall = capAt.get(f.timestamp) ?? Date.now();
        capAt.delete(f.timestamp);
        if (listener) listener(f, wall);
        else f.close();
      },
      error: (e) => {
        tapStats.errors++;
        tapStats.lastError = String(e?.message || e);
        needKey = true;
        dec = null;
      },
    });
    dec.configure({ codec: tapStats.codec, optimizeForLatency: true });
    return dec;
  };

  let ts = 0;
  const transform = new TransformStream({
    transform(frame: any, ctl) {
      tapStats.encoded++;
      const recv = performance.timeOrigin + performance.now();
      tapStats.ring.push([recv, frame.timestamp]);
      if (tapStats.ring.length > 600) tapStats.ring.shift();
      try {
        if (frame.type === 'key') needKey = false;
        if (!needKey && frame.data?.byteLength) {
          const d = decoder();
          ts += 1;
          capAt.set(ts, captureTime(frame.timestamp, recv));
          if (capAt.size > 60) capAt.delete(capAt.keys().next().value as number);
          // 解码一律不跳帧（跳了 P 帧后面全花），处理不过来在输出端丢
          d.decode(new EncodedVideoChunk({ type: frame.type === 'key' ? 'key' : 'delta', timestamp: ts, data: frame.data }));
        }
      } catch (e: any) {
        tapStats.errors++;
        tapStats.lastError = String(e?.message || e);
        needKey = true;
        dec = null;
      }
      ctl.enqueue(frame); // 原样放回，页面上的视频照常播放
    },
  });
  readable.pipeThrough(transform).pipeTo(writable).catch(() => {});
}

export function installLowLatency() {
  if (!window.RTCPeerConnection) return;
  const tapOn = !location.search.includes('tap=off') && 'createEncodedStreams' in RTCRtpReceiver.prototype &&
    typeof VideoDecoder !== 'undefined';
  tapStats.enabled = tapOn;
  const Orig = window.RTCPeerConnection;
  function Patched(this: any, config?: RTCConfiguration, ...rest: any[]) {
    const cfg = tapOn ? { ...(config || {}), encodedInsertableStreams: true } : config;
    const pc: RTCPeerConnection = new (Orig as any)(cfg, ...rest);
    if (tapOn) {
      pc.addEventListener('track', (ev) => {
        if (ev.track.kind === 'video') tapVideo(ev.receiver);
        else passThrough(ev.receiver);
      });
      const addTrack = pc.addTrack.bind(pc);
      pc.addTrack = (...a: Parameters<RTCPeerConnection['addTrack']>) => {
        const s = addTrack(...a);
        passThrough(s);
        return s;
      };
      const addTransceiver = pc.addTransceiver.bind(pc);
      pc.addTransceiver = (...a: Parameters<RTCPeerConnection['addTransceiver']>) => {
        const t = addTransceiver(...a);
        passThrough(t.sender);
        return t;
      };
    }
    const pcs: RTCPeerConnection[] = ((window as any).__jw_pcs ||= []);
    pcs.push(pc);
    if (pcs.length > 5) pcs.shift();
    return pc;
  }
  Patched.prototype = Orig.prototype;
  Object.setPrototypeOf(Patched, Orig); // 保留 generateCertificate 等静态方法
  (window as any).RTCPeerConnection = Patched;
  (window as any).__jw_tap = tapStats;
}

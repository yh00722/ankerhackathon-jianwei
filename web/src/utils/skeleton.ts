/**
 * @name 画面 + 骨架绘制
 * @description 控制台大屏和老人手机（折叠屏展开）共用：把一帧 JPEG 画到 canvas 上，按服务端的旋转设置转正，
 * 再叠上骨架（目标人绿色、其他人灰色）。骨架坐标是服务端换回原始画面的归一化坐标。
 */

export const BONES = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [24, 26], [26, 28], [27, 29], [29, 31], [28, 30], [30, 32],
];

export interface SkeletonView {
  skeletons?: number[][][];
  target?: number | null;
}

export async function drawFrame(c: HTMLCanvasElement, blob: Blob, view: SkeletonView | undefined, rotate: number, width = 960) {
  const bmp = await createImageBitmap(blob);
  try {
    const ctx = c.getContext('2d')!;
    const flip = rotate === 180;
    c.width = width;
    c.height = Math.round((width * bmp.height) / bmp.width);
    ctx.save();
    if (flip) {
      ctx.translate(c.width, c.height);
      ctx.rotate(Math.PI);
    }
    ctx.drawImage(bmp, 0, 0, c.width, c.height);
    ctx.restore();
    const scale = width / 480;
    (view?.skeletons || []).forEach((sk, i) => {
      const pt = (k: number) => {
        const [x, y] = sk[k];
        return flip ? [(1 - x) * c.width, (1 - y) * c.height] : [x * c.width, y * c.height];
      };
      const me = i === view?.target;
      ctx.strokeStyle = me ? '#3ddc97' : '#9aa4b2';
      ctx.lineWidth = (me ? 3 : 2) * scale;
      ctx.lineCap = 'round';
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
    bmp.close();
  }
}

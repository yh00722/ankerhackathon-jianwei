"""从画面判断镜头是否在转动（整体位移检测）。

做法：把每帧缩到 160×90 的灰度图，切成 3×3 九块，每块用相位相关（cv2.phaseCorrelate）求和上一帧的平移，
取各块的中位数作为整体位移。镜头转动时九块一起动；人在画面里走动只影响其中几块，中位数不受影响。
画面里的人（框外扩 15%）先被抹成均值，大半被人挡住的块不参与。
用途：
- 云台指令确认后，不再固定等 4 秒：看到画面动了又停下，就结束「转动中」（更快也更准）；
- 不是我们下的指令造成的转动（手动、追踪），也能识别出来，这段时间的动作一律记「没看全」。

10/16 晚实测：两个人坐在镜头前 1 米内说话、比划时，整幅图相位相关给出 0.2–1.2 像素（96 宽）的「位移」，
置信度还很高，会误报镜头在转；改成分块取中位数。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class MotionConfig:
    size: tuple[int, int] = (160, 90)
    grid: int = 3
    thresh_px: float = 1.0  # 小图上相邻帧整体平移超过这个像素数，算这一帧在动（160 宽下约等于原图 12 像素）
    min_response: float = 0.1  # 每块相位相关的峰值置信度下限（太低说明两帧对不上，这块不计）
    min_tiles: int = 4  # 至少要有这么多块可用，才给结论
    max_masked: float = 0.5  # 一块里被人挡住超过这个比例就不用
    pad: float = 0.15  # 人框外扩比例（关键点框比人的轮廓小）
    hold_s: float = 0.5  # 最后一次检测到位移后，再保持这么久才算停下
    sustain_s: float = 0.5  # 没有我们下的指令时，位移要持续这么久才算镜头在转（防偶发误判）
    max_gap_s: float = 1.0  # 两帧间隔超过它（断流、跳帧）就不比较，重新起算


class GlobalMotion:
    def __init__(self, cfg: MotionConfig | None = None):
        self.cfg = cfg or MotionConfig()
        w, h = self.cfg.size
        g = self.cfg.grid
        self._tiles = [(x * w // g, y * h // g, (x + 1) * w // g, (y + 1) * h // g) for y in range(g) for x in range(g)]
        tw, th = w // g, h // g
        self._win = cv2.createHanningWindow((tw, th), cv2.CV_32F)
        self.reset()

    def reset(self) -> None:
        self._prev: np.ndarray | None = None
        self._prev_t: float | None = None
        self._prev_mask: np.ndarray | None = None
        self.last_motion_t: float | None = None
        self.run_start: float | None = None  # 这一段连续位移的起点
        self.last_shift = 0.0
        self.last_response = 0.0
        self.last_tiles = 0

    def update(self, t: float, rgb: np.ndarray, boxes: list[tuple] | None = None) -> float:
        """输入一帧（RGB）和画面里人的框（同一分辨率的像素坐标），返回整体平移量（小图像素）。"""
        c = self.cfg
        w, h = c.size
        g = cv2.cvtColor(cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32)
        masked = np.zeros((h, w), dtype=bool)
        if boxes:
            sx, sy = w / rgb.shape[1], h / rgb.shape[0]
            for x0, y0, x1, y1 in boxes:
                px, py = (x1 - x0) * c.pad, (y1 - y0) * c.pad
                masked[max(0, int((y0 - py) * sy)):int((y1 + py) * sy) + 1,
                       max(0, int((x0 - px) * sx)):int((x1 + px) * sx) + 1] = True
            if masked.any():
                g[masked] = float(g[~masked].mean()) if (~masked).any() else float(g.mean())
        prev, prev_t, prev_mask = self._prev, self._prev_t, self._prev_mask
        self._prev, self._prev_t, self._prev_mask = g, t, masked
        if prev is None or prev_t is None or t - prev_t > c.max_gap_s:
            return 0.0
        both = masked | prev_mask if prev_mask is not None else masked
        dxs, dys, resps = [], [], []
        for x0, y0, x1, y1 in self._tiles:
            if both[y0:y1, x0:x1].mean() > c.max_masked:
                continue
            a = prev[y0:y1, x0:x1][: self._win.shape[0], : self._win.shape[1]]
            b = g[y0:y1, x0:x1][: self._win.shape[0], : self._win.shape[1]]
            (dx, dy), resp = cv2.phaseCorrelate(a, b, self._win)
            if resp > c.min_response:
                dxs.append(dx)
                dys.append(dy)
                resps.append(resp)
        self.last_tiles = len(dxs)
        if len(dxs) < c.min_tiles:
            self.last_shift, self.last_response = 0.0, 0.0
            return 0.0
        shift = math.hypot(statistics.median(dxs), statistics.median(dys))
        self.last_shift, self.last_response = shift, statistics.median(resps)
        if shift > c.thresh_px:
            if self.last_motion_t is None or t - self.last_motion_t > c.hold_s:
                self.run_start = t
            self.last_motion_t = t
        return shift

    def moving(self, t: float) -> bool:
        return self.last_motion_t is not None and t - self.last_motion_t < self.cfg.hold_s

    def sustained(self, t: float) -> bool:
        """位移持续了一段时间（不是一两帧的偶发误判）。"""
        return (self.moving(t) and self.run_start is not None
                and self.last_motion_t - self.run_start >= self.cfg.sustain_s)

    def moved_since(self, t0: float) -> bool:
        return self.last_motion_t is not None and self.last_motion_t >= t0

"""从画面判断镜头是否在转动（整体位移检测）。

做法：把每帧缩到 96×54 的灰度图，用相位相关（cv2.phaseCorrelate）求相邻两帧的整体平移。
画面里的人先被抹成均值，避免人走动被误判成镜头转动。
用途：
- 云台指令确认后，不再固定等 4 秒：看到画面动了又停下，就结束「转动中」（更快也更准）；
- 不是我们下的指令造成的转动（手动、追踪），也能识别出来，这段时间的动作一律记「没看全」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class MotionConfig:
    size: tuple[int, int] = (96, 54)
    thresh_px: float = 0.6  # 小图上相邻帧整体平移超过这个像素数，算这一帧在动
    min_response: float = 0.05  # 相位相关的峰值置信度下限（太低说明两帧对不上，不计）
    hold_s: float = 0.5  # 最后一次检测到位移后，再保持这么久才算停下
    sustain_s: float = 0.3  # 没有我们下的指令时，位移要持续这么久才算镜头在转（防偶发误判）
    max_gap_s: float = 1.0  # 两帧间隔超过它（断流、跳帧）就不比较，重新起算


class GlobalMotion:
    def __init__(self, cfg: MotionConfig | None = None):
        self.cfg = cfg or MotionConfig()
        self._win = cv2.createHanningWindow(self.cfg.size, cv2.CV_32F)
        self.reset()

    def reset(self) -> None:
        self._prev: np.ndarray | None = None
        self._prev_t: float | None = None
        self.last_motion_t: float | None = None
        self.run_start: float | None = None  # 这一段连续位移的起点
        self.last_shift = 0.0
        self.last_response = 0.0

    def update(self, t: float, rgb: np.ndarray, boxes: list[tuple] | None = None) -> float:
        """输入一帧（RGB）和画面里人的框（同一分辨率的像素坐标），返回整体平移量（小图像素）。"""
        w, h = self.cfg.size
        g = cv2.cvtColor(cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32)
        if boxes:
            sx, sy = w / rgb.shape[1], h / rgb.shape[0]
            mean = float(g.mean())
            for x0, y0, x1, y1 in boxes:
                g[max(0, int(y0 * sy) - 2):int(y1 * sy) + 3, max(0, int(x0 * sx) - 2):int(x1 * sx) + 3] = mean
        prev, prev_t = self._prev, self._prev_t
        self._prev, self._prev_t = g, t
        if prev is None or prev_t is None or t - prev_t > self.cfg.max_gap_s:
            return 0.0
        (dx, dy), resp = cv2.phaseCorrelate(prev, g, self._win)
        shift = math.hypot(dx, dy)
        self.last_shift, self.last_response = shift, resp
        if shift > self.cfg.thresh_px and resp > self.cfg.min_response:
            if self.last_motion_t is None or t - self.last_motion_t > self.cfg.hold_s:
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

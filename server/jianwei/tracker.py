"""目标跟踪：只在本次会话内区分「目标」和「他人」，不做任何身份识别。

- 没有目标时，选授权框内面积最大的人；之后每帧选髋中心离上一帧最近的人。
- 两人距离太近（小于 0.6 个躯干长），或者画面里有多人且目标跳动超过一个躯干长：判为 AMBIGUOUS。
- 他人在旁：另一个人的框与目标框（向外扩一个躯干长）有交叠。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .features import Features


@dataclass
class Selection:
    target: int | None
    ambiguous: bool
    other_near: bool
    persons: int


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class TargetTracker:
    def __init__(self, roi: tuple[float, float, float, float] | None = None):
        self.roi = roi  # 归一化坐标 x0, y0, x1, y1；None 表示整个画面
        self.reset()

    def reset(self) -> None:
        self.last: tuple[float, float] | None = None

    def _in_roi(self, f: Features, w: int, h: int) -> bool:
        if self.roi is None:
            return True
        x, y = f.hip_center[0] / w, f.hip_center[1] / h
        return self.roi[0] <= x <= self.roi[2] and self.roi[1] <= y <= self.roi[3]

    def select(self, feats: list[Features | None], boxes: list[tuple], w: int, h: int) -> Selection:
        persons = len(boxes)
        cands = [i for i, f in enumerate(feats) if f is not None and self._in_roi(f, w, h)]
        if not cands:
            return Selection(None, False, False, persons)
        if self.last is None:
            tgt = max(cands, key=lambda i: (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
            jump = 0.0
        else:
            tgt = min(cands, key=lambda i: _dist(feats[i].hip_center, self.last))
            jump = _dist(feats[tgt].hip_center, self.last)
        f = feats[tgt]
        torso = max(f.torso_px, 1.0)
        others = [i for i in range(persons) if i != tgt]
        too_close = any(feats[i] is not None and _dist(feats[i].hip_center, f.hip_center) < 0.6 * torso
                        for i in others)
        ambiguous = too_close or (len(cands) > 1 and jump > torso)
        x0, y0, x1, y1 = f.bbox
        zone = (x0 - torso, y0 - torso, x1 + torso, y1 + torso)
        near = any(_overlap(boxes[i], zone) for i in others)
        self.last = f.hip_center
        return Selection(tgt, ambiguous, near, persons)

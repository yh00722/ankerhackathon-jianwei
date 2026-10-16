"""姿态状态机：特征 → 坐 / 站 / 过渡 / 不确定，并判断是否「稳定」。

以 f1 为主判据；3D 髋角只在明显矛盾时否决，避免正面机位下髋角估得不准时误判。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .features import Features

SIT, STAND, TRANSITION, UNKNOWN = "sit", "stand", "transition", "unknown"


@dataclass
class Thresholds:
    sit_f1: float = 0.45
    stand_f1: float = 0.65
    sit_hip: float = 135.0
    stand_hip: float = 150.0
    sit_stable_s: float = 0.8
    stand_stable_s: float = 0.5
    window: int = 5


@dataclass
class PostureState:
    t: float
    raw: str
    f1: float | None
    hip: float | None
    knee: float | None
    since: float
    sit_stable: bool
    stand_stable: bool


class PostureClassifier:
    def __init__(self, th: Thresholds | None = None):
        self.th = th or Thresholds()
        self.reset()

    def reset(self) -> None:
        self._buf: deque[tuple[float, float, float | None]] = deque(maxlen=self.th.window)
        self._cls = UNKNOWN
        self._since = 0.0

    def _classify(self, f1: float, hip: float) -> str:
        th = self.th
        hip_ok = not math.isnan(hip)
        if f1 < th.sit_f1 and not (hip_ok and hip > th.stand_hip):
            return SIT
        if f1 > th.stand_f1 and not (hip_ok and hip < th.sit_hip):
            return STAND
        return TRANSITION

    def update(self, t: float, feat: Features | None) -> PostureState:
        if feat is None:
            self._buf.clear()
            cls, f1, hip, knee = UNKNOWN, None, None, None
        else:
            self._buf.append((feat.f1, feat.hip, feat.knee))
            f1 = _median([b[0] for b in self._buf])
            hip = _median([b[1] for b in self._buf if not math.isnan(b[1])])
            knees = [b[2] for b in self._buf if b[2] is not None]
            knee = _median(knees) if knees else None
            cls = self._classify(f1, hip if hip is not None else float("nan"))
        if cls != self._cls:
            self._cls, self._since = cls, t
        dur = t - self._since
        return PostureState(
            t=t,
            raw=cls,
            f1=f1,
            hip=hip,
            knee=knee,
            since=self._since,
            sit_stable=cls == SIT and dur >= self.th.sit_stable_s,
            stand_stable=cls == STAND and dur >= self.th.stand_stable_s,
        )


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

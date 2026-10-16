"""姿态状态机：特征 → 坐 / 站 / 过渡 / 不确定，并判断是否「稳定」。

- 平滑：One Euro 滤波（静止时平滑去抖，动作快时跟得紧），比中位数窗口延迟小。
- 判据：f1 为主，3D 髋角为辅。
  - 没标定时（默认阈值）髋角只在明显矛盾时否决：正面机位下髋角估得不准，不能让它挡住正常判断；
  - 标定后（见 calib.py）用这个人在这个机位下站直时的髋角：判「站」要求髋角接近站直，
    避免弯着腰、没站直就被当成站起来（术后患者常见）；标定时髋角分不开坐站，就不参与判断。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .features import Features

SIT, STAND, TRANSITION, UNKNOWN = "sit", "stand", "transition", "unknown"


@dataclass
class Thresholds:
    sit_f1: float = 0.45  # f1 低于它才可能是坐
    stand_f1: float = 0.65  # f1 高于它才可能是站
    sit_hip: float = 150.0  # 判「坐」时髋角不能超过它（髋角可用时）
    stand_hip: float = 135.0  # 判「站」时髋角至少要到它（髋角可用时）
    sit_stable_s: float = 0.6
    stand_stable_s: float = 0.3
    calibrated: bool = False


class OneEuro:
    """One Euro 滤波（Casiez et al., 2012）。min_cutoff 越小越平滑；beta 越大，快动作时越不滞后。"""

    def __init__(self, min_cutoff: float = 1.5, beta: float = 1.0, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self.x: float | None = None
        self.dx = 0.0
        self.t: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, t: float, x: float) -> float:
        if self.x is None or self.t is None or t <= self.t:
            self.x, self.t, self.dx = x, t, 0.0
            return x
        dt = t - self.t
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


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
        self._f1 = OneEuro(min_cutoff=1.5, beta=2.0)
        self._hip = OneEuro(min_cutoff=1.0, beta=0.05)
        self._knee = OneEuro(min_cutoff=1.0, beta=0.05)
        self.reset()

    def reset(self) -> None:
        for f in (self._f1, self._hip, self._knee):
            f.reset()
        self._cls = UNKNOWN
        self._since = 0.0

    def set_thresholds(self, th: Thresholds) -> None:
        self.th = th

    def _classify(self, f1: float, hip: float | None) -> str:
        th = self.th
        if f1 < th.sit_f1 and (hip is None or hip <= th.sit_hip):
            return SIT
        if f1 > th.stand_f1 and (hip is None or hip >= th.stand_hip):
            return STAND
        return TRANSITION

    def update(self, t: float, feat: Features | None) -> PostureState:
        if feat is None:
            for f in (self._f1, self._hip, self._knee):
                f.reset()
            cls, f1, hip, knee = UNKNOWN, None, None, None
        else:
            f1 = self._f1(t, feat.f1)
            hip = self._hip(t, feat.hip) if not math.isnan(feat.hip) else None
            knee = self._knee(t, feat.knee) if feat.knee is not None else None
            cls = self._classify(f1, hip)
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

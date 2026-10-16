"""按机位标定坐、站阈值（每个预置位一份）。

为什么要标定：f1 是画面里大腿竖直投影 ÷ 躯干长，摄像头高度、俯角、人和椅子的朝向不同，
坐着和站着的 f1 都会变。默认阈值（坐 < 0.45、站 > 0.65）是按侧前方 30–45° 机位估的，
换了机位可能偏。

流程：请坐好不动 → 连续 3 秒稳定 → 请站起来站直不动 → 连续 3 秒稳定 → 算阈值。
- f1 阈值取坐、站基线之间的 35% / 70%；
- 坐站髋角差够大（≥ 25°）时，给出髋角阈值（站要接近自己站直时的髋角），否则髋角不参与判断。
只存几个数字（坐 / 站的 f1、髋角中位数），不存画面和关键点。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .posture import Thresholds

PROMPTS = {
    "sit": "请在椅子上坐好，保持不动",
    "stand": "请站起来，站直，保持不动",
    "done": "标定完成",
    "failed": "标定没有完成",
    "aborted": "已取消标定",
}


@dataclass
class CalibConfig:
    collect_s: float = 3.0  # 坐、站各需要连续稳定多久
    max_gap_s: float = 0.4  # 采集中允许的最长缺口
    max_spread_f1: float = 0.12  # 采集窗口内 f1 的四分位距上限（超过说明还在动）
    stand_jump: float = 0.15  # 站姿采集要等 f1 比坐姿基线高出这么多（人确实站起来了）
    min_range: float = 0.2  # 坐站 f1 差不到这个数，说明这个机位分不清坐站
    hip_min_range: float = 25.0
    sit_frac: float = 0.35
    stand_frac: float = 0.70
    timeout_s: float = 45.0


def thresholds_from(c: dict | None) -> Thresholds:
    """由标定结果（坐 / 站基线）算阈值；没有标定时用默认阈值。"""
    if not c:
        return Thresholds()
    cfg = CalibConfig()
    sit, stand = c["sit_f1"], c["stand_f1"]
    rng = stand - sit
    th = Thresholds(sit_f1=sit + cfg.sit_frac * rng, stand_f1=sit + cfg.stand_frac * rng, calibrated=True)
    sh, st = c.get("sit_hip"), c.get("stand_hip")
    if sh is not None and st is not None and st - sh >= cfg.hip_min_range:
        hr = st - sh
        th.sit_hip = sh + 0.5 * hr
        th.stand_hip = st - max(12.0, 0.3 * hr)
    else:  # 这个机位下 3D 髋角估得不准：不参与判断
        th.sit_hip, th.stand_hip = 999.0, 0.0
    return th


def _spread(xs: list[float]) -> float:
    if len(xs) < 4:
        return 0.0
    q = statistics.quantiles(xs, n=4)
    return q[2] - q[0]


@dataclass
class Calibrator:
    cfg: CalibConfig = field(default_factory=CalibConfig)

    def __post_init__(self) -> None:
        self.phase = "idle"
        self.preset: int | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.win: list[tuple[float, float, float | None]] = []

    @property
    def active(self) -> bool:
        return self.phase in ("sit", "stand")

    def start(self, t: float, preset: int) -> dict:
        self.phase, self.phase_t, self.preset = "sit", t, preset
        self.win, self.result, self.error = [], None, None
        self.sit: tuple[float, float | None] | None = None
        return self._event()

    def cancel(self, t: float) -> dict:
        self.phase = "aborted"
        return self._event()

    def status(self, t: float) -> dict:
        d = {"phase": self.phase, "prompt": PROMPTS.get(self.phase, ""), "preset": self.preset,
             "progress": round(self._progress(), 2)}
        if self.error:
            d["error"] = self.error
        if self.result:
            d["result"] = self.result
        return d

    def update(self, t: float, observable: bool, f1: float | None, hip: float | None) -> list[dict]:
        """每帧调用：f1、hip 用目标的原始特征（不经过平滑）。返回本帧产生的事件。"""
        if not self.active:
            return []
        c = self.cfg
        if t - self.phase_t > c.timeout_s:
            return [self._fail("超时：请确认人在画面里、坐站动作做完整")]
        if not observable or f1 is None:
            self.win = []
            return []
        if self.phase == "stand" and self.sit and f1 < self.sit[0] + c.stand_jump:
            self.win = []  # 还没站起来
            return []
        if self.win and t - self.win[-1][0] > c.max_gap_s:
            self.win = []
        self.win.append((t, f1, hip))
        while self.win and self.win[0][0] < t - c.collect_s:
            self.win.pop(0)
        f1s = [w[1] for w in self.win]
        if _spread(f1s) > c.max_spread_f1:  # 还在动：从现在重新计
            self.win = self.win[-1:]
            return []
        if self.win[-1][0] - self.win[0][0] < c.collect_s - 0.1:
            return []
        hips = [w[2] for w in self.win if w[2] is not None]
        med = (statistics.median(f1s), statistics.median(hips) if hips else None)
        if self.phase == "sit":
            self.sit = med
            self.phase, self.phase_t, self.win = "stand", t, []
            return [self._event()]
        # 站姿采完
        if med[0] - self.sit[0] < c.min_range:
            return [self._fail("这个机位下坐和站差别太小，请把摄像头放到侧前方 30–45°")]
        self.result = {"preset": self.preset, "sit_f1": round(self.sit[0], 3), "stand_f1": round(med[0], 3),
                       "sit_hip": None if self.sit[1] is None else round(self.sit[1], 1),
                       "stand_hip": None if med[1] is None else round(med[1], 1)}
        self.phase = "done"
        return [self._event()]

    def _progress(self) -> float:
        if not self.active or len(self.win) < 2:
            return 0.0
        return min(1.0, (self.win[-1][0] - self.win[0][0]) / self.cfg.collect_s)

    def _fail(self, msg: str) -> dict:
        self.phase, self.error = "failed", msg
        return self._event()

    def _event(self) -> dict:
        e = {"kind": "calib", "phase": self.phase, "prompt": PROMPTS.get(self.phase, ""), "preset": self.preset}
        if self.result:
            e["result"] = self.result
        if self.error:
            e["error"] = self.error
        return e

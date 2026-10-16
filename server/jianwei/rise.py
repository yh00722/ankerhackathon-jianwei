"""起身事件状态机：完整看到 / 没看全（口径见 TASKS.md）。

| 当前状态 | 条件 | 下一状态 | 输出 |
|---|---|---|---|
| 未知 | 可观测且稳定坐 | 已坐 | — |
| 已坐 | 姿态进入过渡 / 站 | 起身中 | 记下坐姿基线 |
| 起身中 | 稳定站，期间无缺口、无转动、目标明确 | 已站 | 完整起身 + 用时 |
| 起身中 | 缺口 > 0.3 秒、镜头转动、目标不明确 | 未知 | 没看全（原因） |
| 已坐 | 不可观测 > 0.3 秒 | 未知（带「坐过」标记） | — |
| 未知（带标记） | 恢复可观测时已经稳定站 | 已站 | 没看全：中间没看到 |
| 未知（无标记） | 一出现就是站着 | 已站 | 不记 |
| 已站 | 稳定坐 | 已坐 | — |
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .posture import SIT, STAND, TRANSITION, PostureState

UNKNOWN, SEATED, RISING, STANDING = "unknown", "seated", "rising", "standing"

# 立即判「没看全」的原因：只要在起身过程中出现一次就算
IMMEDIATE = {"CAMERA_MOVING", "AMBIGUOUS"}


@dataclass
class RiseConfig:
    gap_s: float = 0.3  # 起身中允许的最长缺口
    marker_s: float = 10.0  # 「坐过」标记的有效期
    near_before_s: float = 2.0  # 起身前多少秒内有他人在旁也算
    min_amp: float = 0.15  # 坐、站基线差太小时不给用时
    hist_s: float = 12.0


@dataclass
class RiseEvent:
    complete: bool
    t_start: float
    t_end: float
    duration_s: float | None = None
    reason: str | None = None
    other_person: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": "rise",
            "complete": self.complete,
            "t_start": round(self.t_start, 3),
            "t_end": round(self.t_end, 3),
            "duration_s": None if self.duration_s is None else round(self.duration_s, 2),
            "reason": self.reason,
            "other_person": self.other_person,
        }


@dataclass
class SitBout:
    t_start: float
    t_end: float
    truncated: bool  # 被「没看到」截断，真实时长可能更长

    def as_dict(self) -> dict:
        return {
            "kind": "sit_bout",
            "t_start": round(self.t_start, 3),
            "t_end": round(self.t_end, 3),
            "duration_s": round(self.t_end - self.t_start, 1),
            "truncated": self.truncated,
        }


@dataclass
class RiseDetector:
    cfg: RiseConfig = field(default_factory=RiseConfig)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = UNKNOWN
        self.last_obs_t: float | None = None
        self.seat_start: float | None = None
        self.marker_until: float | None = None
        self.marker_since = 0.0
        self.marker_reason: str | None = None
        self.rise_start = 0.0
        self.sit_base: float | None = None
        self.hist: deque[tuple[float, float]] = deque()
        self.near: deque[tuple[float, bool]] = deque()
        self.flags: deque[tuple[float, str]] = deque()  # 镜头转动、目标不明确出现的时刻

    # ---- 对外接口 ----

    def update(self, t: float, observable: bool, reason: str | None, ps: PostureState | None,
               other_near: bool = False) -> list:
        """每处理一帧（或断流心跳）调用一次，返回本帧产生的事件。"""
        self.near.append((t, other_near))
        self._prune(t)
        if not observable:
            if reason in IMMEDIATE:
                self.flags.append((t, reason))
            return self._on_unobservable(t, reason or "NO_PERSON")
        assert ps is not None
        out: list = []
        self.last_obs_t = t
        if ps.f1 is not None:
            self.hist.append((t, ps.f1))

        if self.state == UNKNOWN:
            if ps.sit_stable:
                self._enter_seated(ps.since)
            elif ps.stand_stable:
                if self.marker_until is not None and t <= self.marker_until:
                    out.append(RiseEvent(False, self.marker_since, t, reason=self.marker_reason or "GAP",
                                         other_person=self._near_since(self.marker_since)))
                self.marker_until = None
                self.state = STANDING
        elif self.state == SEATED:
            if ps.raw in (TRANSITION, STAND):
                out += self._close_sit_bout(t, truncated=False)
                self.state = RISING
                self.rise_start = t
                self.sit_base = self._median_f1(self.seat_start or t - 1.0, t)
        elif self.state == RISING:
            if ps.stand_stable:
                out.append(self._finish_rise(t, ps))
                self.state = STANDING
            elif ps.sit_stable:
                # 没站起来又坐回去：不计次数（失败的起身尝试暂不识别）
                self._enter_seated(ps.since)
        elif self.state == STANDING:
            if ps.sit_stable:
                self._enter_seated(ps.since)
        return [e.as_dict() for e in out]

    def current_sit_s(self, t: float) -> float | None:
        """当前这段连续坐着已多久（只在可观测且处于已坐状态时有值）。"""
        if self.state == SEATED and self.seat_start is not None:
            return t - self.seat_start
        return None

    # ---- 内部 ----

    def _enter_seated(self, since: float) -> None:
        self.state = SEATED
        self.seat_start = since
        self.marker_until = None

    def _close_sit_bout(self, t: float, truncated: bool) -> list:
        if self.seat_start is None:
            return []
        bout = SitBout(self.seat_start, t, truncated)
        self.seat_start = None
        return [bout] if bout.t_end - bout.t_start >= 1.0 else []

    def _on_unobservable(self, t: float, reason: str) -> list:
        out: list = []
        gap = t - self.last_obs_t if self.last_obs_t is not None else 0.0
        if self.state == RISING and (reason in IMMEDIATE or gap > self.cfg.gap_s):
            out.append(RiseEvent(False, self.rise_start, t, reason=reason,
                                 other_person=self._near_since(self.rise_start)))
            self.state = UNKNOWN
            self.marker_until = None
        elif self.state == SEATED and gap > self.cfg.gap_s:
            last = self.last_obs_t or t
            out += self._close_sit_bout(last, truncated=True)
            self.state = UNKNOWN
            self.marker_since = last
            self.marker_until = last + self.cfg.marker_s
            self.marker_reason = reason
        elif self.state == STANDING and gap > self.cfg.gap_s:
            self.state = UNKNOWN
        return [e.as_dict() for e in out]

    def _finish_rise(self, t: float, ps: PostureState) -> RiseEvent:
        stand_base = self._median_f1(ps.since, t)
        dur = None
        if self.sit_base is not None and stand_base is not None and stand_base - self.sit_base >= self.cfg.min_amp:
            dur = self._rise_duration(self.sit_base, stand_base, t)
        onset = t - dur if dur is not None else self.rise_start - 1.0
        near = self._near_since(self.rise_start)
        flagged = [r for ti, r in self.flags if onset - 0.2 <= ti <= t]
        if flagged:
            return RiseEvent(False, self.rise_start, t, reason=flagged[0], other_person=near)
        return RiseEvent(True, self.rise_start, t, duration_s=dur, other_person=near)

    def _rise_duration(self, sit_b: float, stand_b: float, t_end: float) -> float | None:
        amp = stand_b - sit_b
        pts = [(ti, (fi - sit_b) / amp) for ti, fi in self.hist if ti <= t_end]
        t90 = next((ti for ti, p in pts if ti >= self.rise_start - 1.0 and p >= 0.9), None)
        if t90 is None:
            return None
        t10 = None
        for ti, p in pts:
            if ti > t90:
                break
            if p <= 0.1:
                t10 = ti
        return None if t10 is None else t90 - t10

    def _median_f1(self, t0: float, t1: float) -> float | None:
        xs = sorted(f for ti, f in self.hist if t0 <= ti <= t1)
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    def _near_since(self, t0: float) -> bool:
        return any(v for ti, v in self.near if ti >= t0 - self.cfg.near_before_s)

    def _prune(self, t: float) -> None:
        while self.hist and self.hist[0][0] < t - self.cfg.hist_s:
            self.hist.popleft()
        while self.near and self.near[0][0] < t - self.cfg.hist_s:
            self.near.popleft()
        while self.flags and self.flags[0][0] < t - self.cfg.hist_s:
            self.flags.popleft()

"""起身事件状态机：完整看到 / 没看全（口径见 TASKS.md）。

| 当前状态 | 条件 | 下一状态 | 输出 |
|---|---|---|---|
| 未知 | 可观测且稳定坐 | 已坐 | — |
| 已坐 | 姿态进入过渡 / 站 | 起身中 | 记下坐姿基线 |
| 起身中 | 稳定站，期间无缺口、无转动、目标明确 | 已站 | 这段坐结束；等站稳（f1 不再上升）后给出完整起身 + 用时 |
| 起身中 | 缺口 > 0.3 秒、镜头转动、目标不明确 | 未知 | 没看全（原因）；这段坐结束 |
| 起身中 | 又稳定坐回去（没站起来） | 已坐 | 不记；这段坐接着算 |
| 已坐 | 不可观测 > 0.3 秒 | 未知（带「坐过」标记） | — |
| 未知（带标记） | 恢复可观测时已经稳定站 | 已站 | 没看全：中间没看到 |
| 未知（无标记） | 一出现就是站着 | 已站 | 不记 |
| 已站 | 稳定坐 | 已坐 | — |
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .posture import STAND, TRANSITION, PostureState

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
    # 站姿基线要等人站稳再取：姿态判为「站」时可能还在往上站（术后起身慢），这时取基线会把用时算短
    plateau_s: float = 0.5  # 站稳的判断窗口
    plateau_tol: float = 0.03  # 最近这个窗口里 f1 的最高值比之前的最高值高不过它，算不再往上站
    confirm_max_s: float = 2.0  # 最多等这么久，还没站稳就用已有数据算


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
        self.pending: tuple[float, float] | None = None  # 已判为站、等站稳再出结果：(判为稳定站的时刻, 站姿起点)
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
                self.state = RISING
                self.rise_start = t
                # 坐姿基线取 25% 分位：窗口末尾已经开始慢慢起身（还没过阈值），用中位数会偏高、把用时算短
                self.sit_base = self._quantile_f1(self.seat_start if self.seat_start is not None else t - 1.0, t, 0.25)
        elif self.state == RISING:
            if ps.stand_stable:
                out += self._close_sit_bout(self.rise_start, truncated=False)
                self.pending = (t, ps.since)
                self.state = STANDING
            elif ps.sit_stable:
                # 没站起来又坐回去（或只是姿态抖了一下）：不计次数，这段坐接着算
                self.state = SEATED
        elif self.state == STANDING:
            if self.pending and (ps.sit_stable or self._plateau(t) or t - self.pending[0] >= self.cfg.confirm_max_s):
                out.append(self._finish_rise(t))
            if ps.sit_stable:
                self._enter_seated(ps.since)
        return [e.as_dict() for e in out]

    def current_sit_s(self, t: float) -> float | None:
        """当前这段连续坐着已多久（只在可观测且处于已坐 / 正要起身状态时有值）。"""
        if self.state in (SEATED, RISING) and self.seat_start is not None:
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
        if self.pending:  # 起身已经完整看到，只是没等到站稳：用已有数据出结果
            out.append(self._finish_rise(self.last_obs_t or t))
        gap = t - self.last_obs_t if self.last_obs_t is not None else 0.0
        if self.state == RISING and (reason in IMMEDIATE or gap > self.cfg.gap_s):
            out += self._close_sit_bout(self.rise_start, truncated=False)
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

    def _plateau(self, t: float) -> bool:
        """站稳：最近 plateau_s 秒里 f1 的最高值，不比之前（起身以来）的最高值高出 plateau_tol。
        用「最高值不再上升」而不是「起伏很小」：对关键点抖动不敏感，慢慢站起的最后一段也不会提前判稳。"""
        cut = t - self.cfg.plateau_s
        old = [f for ti, f in self.hist if self.rise_start <= ti < cut]
        new = [f for ti, f in self.hist if ti >= cut]
        return bool(old) and len(new) >= 3 and max(new) - max(old) <= self.cfg.plateau_tol

    def _finish_rise(self, t: float) -> RiseEvent:
        """t：用到的最后一帧。事件的结束时刻记为判为稳定站的时刻。"""
        t_stable, since = self.pending
        self.pending = None
        stand_base = self._median_f1(max(since, t - self.cfg.plateau_s), t) or self._median_f1(since, t)
        dur = None
        if self.sit_base is not None and stand_base is not None and stand_base - self.sit_base >= self.cfg.min_amp:
            dur = self._rise_duration(self.sit_base, stand_base, t)
        onset = t_stable - dur if dur is not None else self.rise_start - 1.0
        near = self._near_since(self.rise_start)
        flagged = [r for ti, r in self.flags if onset - 0.2 <= ti <= t_stable]
        if flagged:
            return RiseEvent(False, self.rise_start, t_stable, reason=flagged[0], other_person=near)
        return RiseEvent(True, self.rise_start, t_stable, duration_s=dur, other_person=near)

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

    def _quantile_f1(self, t0: float, t1: float, q: float) -> float | None:
        xs = sorted(f for ti, f in self.hist if t0 <= ti <= t1)
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None

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

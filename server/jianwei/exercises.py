"""康复训练动作：坐位伸膝、坐位屈膝、站立平衡、2 分钟原地抬膝、扶台面转移重心。

共同口径（与起身识别、30 秒坐站一致）：
- 只记测得准的量：次数、保持秒数。角度只在关键点看得清时给，并标「实验性」；平衡不给晃动幅度。
- 一次动作要完整看到才计入；中途看不到记「没看全」不计次。他人在旁、镜头转动：整组结果保留，但标无效、不进趋势。
- 不判断好坏、不给建议。每个动作由康复师在训练计划里打开（store.plan），系统不默认推荐。

流程：准备（坐好 / 站好）→ 进行中（计数）→ 完成 / 取消。每个动作一个类，engine 同一时间只跑一个。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np

from .kin import SIDE, SIDE_TEXT, Body
from .posture import OneEuro, PostureState

INVALID_TEXT = {
    "OTHER_PERSON": "旁边有人",
    "CAMERA_MOVING": "镜头转动",
    "LEFT_FRAME": "中途离开画面",
    "SAT_DOWN": "中途坐下了",
    "NOT_FRONTAL": "机位不是正面",
}


@dataclass
class Obs:
    """一帧（或断流心跳）给训练动作的输入。body 只在可观测且有目标时有值。"""

    t: float
    observable: bool
    reason: str | None
    ps: PostureState | None
    body: Body | None
    other_near: bool = False


class Exercise:
    kind = ""
    name = ""
    seated = True  # True：坐着做；False：站着做
    view = "side"  # 需要的机位：side 训练位（侧前方）/ front 正面训练位
    limit_s = 240.0  # 进行中最长时长
    prep_timeout_s = 90.0
    leave_s = 2.0
    DEFAULTS: dict = {}

    def __init__(self, params: dict | None = None):
        self.p = {**self.DEFAULTS, **(params or {})}
        self.phase = "idle"
        self.phase_t = 0.0
        self.invalid: set[str] = set()
        self.unobs_since: float | None = None
        self.last_note: str | None = None

    # ---- 对外 ----

    @property
    def active(self) -> bool:
        return self.phase not in ("idle", "done", "aborted")

    def start(self, t: float) -> dict:
        self.started_at = t
        return self._goto("prep", t)

    def cancel(self, t: float) -> list[dict]:
        """取消：进行中已经做了的保留成结果（标「提前结束」），还在准备阶段就直接取消。"""
        if self.phase == "running":
            return [self._finish(t, completed=False)]
        self.phase, self.phase_t = "aborted", t
        return [self._phase_event()]

    def update(self, t: float, o: Obs) -> list[dict]:
        if not self.active:
            return []
        if self.phase == "prep":
            if t - self.phase_t > self.prep_timeout_s:
                self.phase = "aborted"
                return [{**self._phase_event(), "text": f"{self.name}：等了 {int(self.prep_timeout_s)} 秒没有准备好，已取消"}]
            return self._prep(t, o)
        out: list[dict] = []
        if self.phase == "running":
            if o.other_near:
                self.invalid.add("OTHER_PERSON")
            if not o.observable:
                if o.reason == "CAMERA_MOVING":
                    self.invalid.add("CAMERA_MOVING")
                if self.unobs_since is None:
                    self.unobs_since = t
            else:
                self.unobs_since = None
            out += self._run(t, o)
            if self.phase == "running" and t - self.phase_t >= self.limit_s:
                out.append(self._finish(t, completed=self._completed()))
        else:
            out += self._run(t, o)  # 子类自己的中间阶段（倒数等）
        return out

    def status(self, t: float) -> dict:
        d = {"exercise": self.kind, "name": self.name, "phase": self.phase, "prompt": self.prompt(),
             "invalid": sorted(self.invalid)}
        if self.phase == "running":
            d["elapsed_s"] = round(t - self.phase_t, 1)
        return d

    def prompt(self) -> str:
        return self.PROMPTS.get(self.phase, "").format(**self.p) if hasattr(self, "PROMPTS") else ""

    # ---- 子类实现 ----

    def _prep(self, t: float, o: Obs) -> list[dict]:
        raise NotImplementedError

    def _run(self, t: float, o: Obs) -> list[dict]:
        raise NotImplementedError

    def _metrics(self) -> dict:
        raise NotImplementedError

    def _completed(self) -> bool:
        return True

    # ---- 共用 ----

    def _goto(self, phase: str, t: float) -> dict:
        self.phase, self.phase_t = phase, t
        return self._phase_event()

    def _phase_event(self) -> dict:
        return {"kind": "ex_phase", "exercise": self.kind, "name": self.name, "phase": self.phase,
                "prompt": self.prompt()}

    def _note(self, text: str) -> list[dict]:
        """同一条提示不重复推送。"""
        if text == self.last_note:
            return []
        self.last_note = text
        return [{"kind": "ex_note", "exercise": self.kind, "text": text}]

    def _left_frame(self, t: float) -> bool:
        return self.unobs_since is not None and t - self.unobs_since > self.leave_s

    def _finish(self, t: float, completed: bool) -> dict:
        self.phase, self.phase_t = "done", t
        m = self._metrics()
        return {"kind": "ex_result", "exercise": self.kind, "name": self.name, **m,
                "completed": completed, "valid": not self.invalid,
                "invalid_reasons": sorted(self.invalid), "params": self.p}


def _sides(p: dict) -> tuple[str, ...]:
    return ("left", "right") if p.get("side", "both") == "both" else (p["side"],)


# =====================================================================
# 坐位伸膝（长弧股四头肌练习）：坐着，把小腿抬平、保持，再放下
# =====================================================================


@dataclass
class _Leg:
    st: str = "down"  # down / up
    up_t: float = 0.0
    drop_t: float | None = None
    peak: float = 0.0
    low: float = 180.0
    gap: bool = False
    last_seen: float = 0.0
    vis_min: float = 1.0
    reps: int = 0
    filt: OneEuro = field(default_factory=lambda: OneEuro(min_cutoff=1.0, beta=0.05))
    prog: float | None = None  # 抬到多高：0 = 放下线，1 = 开始计保持的线（给训练界面画进度）
    prog_t: float = -1.0


def _knee_live(legs: dict, t: float, hold_s: float) -> dict:
    """训练界面用：现在该做哪一步（lift 抬起 / hold 保持 / lower 放下），以及抬到多高。"""
    d: dict = {}
    live = [(leg.prog, s) for s, leg in legs.items() if leg.prog is not None and t - leg.prog_t < 1.0]
    if live:
        prog, side = max(live)
        d["progress"], d["active_side"] = round(max(0.0, min(1.3, prog)), 2), side
    up = [leg for leg in legs.values() if leg.st == "up"]
    if not up:
        d["stage"] = "lift"
    elif any(leg.drop_t is None and t - leg.up_t < hold_s for leg in up):
        d["stage"] = "hold"
    else:
        d["stage"] = "lower"
    return d


class KneeExtension(Exercise):
    kind, name = "knee_ext", "坐位伸膝"
    DEFAULTS = {"target_reps": 10, "hold_s": 3.0, "side": "both"}
    UP = 150.0  # 膝角 ≥ 150° 算抬平，开始计保持
    DROP = 142.0  # 降到这以下保持结束
    DOWN = 125.0  # 降到这以下算放下，这一次完成
    MIN_HOLD = 0.3  # 保持不到这么久不算一次（关键点抖动）
    ANGLE_VIS = 0.7  # 整次动作关键点可见度都不低于它，才给角度
    PROMPTS = {
        "prep": "请在椅子上坐好，把要练的那条腿朝向摄像头",
        "running": "慢慢把小腿抬平，保持 {hold_s:g} 秒，再慢慢放下",
        "done": "完成",
        "aborted": "已取消",
    }

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.legs = {s: _Leg() for s in _sides(self.p)}
        self.holds: list[dict] = []
        self.unseen = 0
        self.stood = False

    def _prep(self, t, o):
        if o.observable and o.ps and o.ps.sit_stable and t - o.ps.since >= 0.6:
            return [self._goto("running", t)]
        return []

    def _run(self, t, o):
        out: list[dict] = []
        sitting = o.ps is not None and o.ps.raw == "sit"
        if o.observable and o.ps and o.ps.stand_stable:
            self.stood = True  # 中途站起来：已经数过的照算，只在结果里注明
        if o.observable and not sitting:
            # 站着时膝盖本来就是直的：不计，也把每条腿的状态复位，免得坐回去时被算成一次
            for leg in self.legs.values():
                leg.st, leg.drop_t = "down", None
            if o.ps and o.ps.raw == "stand":
                out += self._note("请坐下继续")
            return out
        for side, leg in self.legs.items():
            a = o.body.knee_angle(side) if (o.observable and o.body) else None
            if a is None:
                if leg.st == "up" and t - leg.last_seen > 0.5:
                    leg.gap = True
                continue
            a = leg.filt(t, a)
            leg.last_seen = t
            leg.prog, leg.prog_t = (a - self.DOWN) / (self.UP - self.DOWN), t
            vis = o.body.leg_vis(side)
            if leg.st == "down":
                if a >= self.UP:
                    leg.st, leg.up_t, leg.drop_t, leg.peak, leg.gap, leg.vis_min = "up", t, None, a, False, vis
            else:
                leg.peak = max(leg.peak, a)
                leg.vis_min = min(leg.vis_min, vis)
                if leg.drop_t is None and a < self.DROP:
                    leg.drop_t = t
                elif leg.drop_t is not None and a >= self.UP:
                    leg.drop_t = None  # 又抬回去了，保持接着算
                if a < self.DOWN:
                    out += self._rep(side, leg, t)
                    leg.st = "down"
        if self._completed():
            out.append(self._finish(t, completed=True))
        return out

    def _rep(self, side: str, leg: _Leg, t: float) -> list[dict]:
        hold = (leg.drop_t if leg.drop_t is not None else t) - leg.up_t
        if leg.gap:
            self.unseen += 1
            return [{"kind": "ex_rep", "exercise": self.kind, "counted": False, "side": side,
                     "text": f"{SIDE_TEXT[side]}这次没看全，没有计入"}]
        if hold < self.MIN_HOLD:
            return []
        leg.reps += 1
        angle = round(leg.peak) if leg.vis_min >= self.ANGLE_VIS else None
        ok = hold >= self.p["hold_s"] - 0.05
        self.holds.append({"side": side, "hold_s": round(hold, 1), "ok": ok, "angle": angle})
        n = self.total()
        text = f"第 {n} 次（{SIDE_TEXT[side]}），保持 {hold:.1f} 秒" + ("" if ok else f"，不到 {self.p['hold_s']:g} 秒")
        return [{"kind": "ex_rep", "exercise": self.kind, "counted": True, "side": side, "reps": n,
                 "hold_s": round(hold, 1), "ok": ok, "angle": angle, "text": text}]

    def total(self) -> int:
        return sum(leg.reps for leg in self.legs.values())

    def _completed(self) -> bool:
        return self.total() >= self.p["target_reps"]

    def status(self, t):
        d = super().status(t)
        d.update(reps=self.total(), target_reps=self.p["target_reps"], hold_target_s=self.p["hold_s"],
                 by_side={s: leg.reps for s, leg in self.legs.items()})
        holding = [t - leg.up_t for leg in self.legs.values() if leg.st == "up" and leg.drop_t is None]
        d["holding_s"] = round(max(holding), 1) if holding else None
        if self.phase == "running":
            d.update(_knee_live(self.legs, t, self.p["hold_s"]))
        return d

    def _metrics(self) -> dict:
        hs = [h["hold_s"] for h in self.holds]
        angles = {s: max((h["angle"] for h in self.holds if h["side"] == s and h["angle"] is not None), default=None)
                  for s in self.legs}
        return {"reps": self.total(), "target_reps": self.p["target_reps"],
                "by_side": {s: leg.reps for s, leg in self.legs.items()},
                "hold_target_s": self.p["hold_s"], "holds": self.holds,
                "hold_mean_s": round(statistics.mean(hs), 1) if hs else None,
                "reps_hold_ok": sum(h["ok"] for h in self.holds),
                "angle_max_experimental": angles, "unseen": self.unseen, "stood_up": self.stood}


# =====================================================================
# 坐位屈膝（只适用于膝置换）：坐着，把脚往椅子下面收，保持，再放回
# =====================================================================


class KneeFlexion(Exercise):
    kind, name = "knee_flex", "坐位屈膝"
    DEFAULTS = {"target_reps": 10, "hold_s": 3.0, "side": "both"}
    IN, HOLD, OUT = 15.0, 10.0, 5.0  # 比静坐时多屈 15° 算一次开始；多屈不到 10° 保持结束；回到 5° 以内算放回
    MIN_HOLD = 0.3
    ANGLE_VIS = 0.7
    PROMPTS = {
        "prep": "请在椅子上坐好，脚平放在地上，要练的那条腿朝向摄像头",
        "running": "把脚慢慢往椅子下面收，保持 {hold_s:g} 秒，再放回原处",
        "done": "完成",
        "aborted": "已取消",
    }

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.legs = {s: _Leg() for s in _sides(self.p)}
        self.base: dict[str, float | None] = {s: None for s in self.legs}
        self.base_samples: dict[str, list[float]] = {s: [] for s in self.legs}
        self.holds: list[dict] = []
        self.unseen = 0
        self.stood = False

    def _prep(self, t, o):
        """静坐、脚平放时的膝角作基线：坐稳以后取约 0.6 秒的中位数。"""
        if not (o.observable and o.ps and o.ps.sit_stable):
            for xs in self.base_samples.values():
                xs.clear()
            return []
        for side in self.legs:
            a = o.body.knee_angle(side) if o.body else None
            if a is not None:
                self.base_samples[side].append(a)
        if t - o.ps.since >= 0.6 and any(len(xs) >= 8 for xs in self.base_samples.values()):
            for side, xs in self.base_samples.items():
                self.base[side] = statistics.median(xs) if len(xs) >= 8 else None
            return [self._goto("running", t)]
        return []

    def _run(self, t, o):
        out: list[dict] = []
        if o.observable and o.ps and o.ps.stand_stable:
            self.stood = True
        if o.observable and o.ps and o.ps.raw != "sit":
            for leg in self.legs.values():
                leg.st, leg.drop_t = "down", None
            if o.ps.raw == "stand":
                out += self._note("请坐下继续")
            return out
        for side, leg in self.legs.items():
            a = o.body.knee_angle(side) if (o.observable and o.body) else None
            if a is None:
                if leg.st == "up" and t - leg.last_seen > 0.5:
                    leg.gap = True
                continue
            a = leg.filt(t, a)
            leg.last_seen = t
            base = self.base[side]
            if base is None:  # 准备阶段这条腿没看清：开始后补取基线
                self.base_samples[side].append(a)
                if len(self.base_samples[side]) >= 10:
                    self.base[side] = statistics.median(self.base_samples[side])
                continue
            leg.prog, leg.prog_t = (base - a - self.OUT) / (self.IN - self.OUT), t
            vis = o.body.leg_vis(side)
            if leg.st == "down":
                if a <= base - self.IN:
                    leg.st, leg.up_t, leg.drop_t, leg.low, leg.gap, leg.vis_min = "up", t, None, a, False, vis
            else:
                leg.low = min(leg.low, a)
                leg.vis_min = min(leg.vis_min, vis)
                if leg.drop_t is None and a > base - self.HOLD:
                    leg.drop_t = t
                elif leg.drop_t is not None and a <= base - self.IN:
                    leg.drop_t = None
                if a >= base - self.OUT:
                    out += self._rep(side, leg, t)
                    leg.st = "down"
        if self.total() >= self.p["target_reps"]:
            out.append(self._finish(t, completed=True))
        return out

    def _rep(self, side: str, leg: _Leg, t: float) -> list[dict]:
        hold = (leg.drop_t if leg.drop_t is not None else t) - leg.up_t
        if leg.gap:
            self.unseen += 1
            return [{"kind": "ex_rep", "exercise": self.kind, "counted": False, "side": side,
                     "text": f"{SIDE_TEXT[side]}这次没看全（脚可能被椅子挡住了），没有计入"}]
        if hold < self.MIN_HOLD:
            return []
        leg.reps += 1
        flex = round(180 - leg.low) if leg.vis_min >= self.ANGLE_VIS else None  # 临床屈曲角 = 180° − 膝关节夹角
        ok = hold >= self.p["hold_s"] - 0.05
        self.holds.append({"side": side, "hold_s": round(hold, 1), "ok": ok, "flexion": flex})
        n = self.total()
        text = f"第 {n} 次（{SIDE_TEXT[side]}），保持 {hold:.1f} 秒" + ("" if ok else f"，不到 {self.p['hold_s']:g} 秒")
        return [{"kind": "ex_rep", "exercise": self.kind, "counted": True, "side": side, "reps": n,
                 "hold_s": round(hold, 1), "ok": ok, "flexion": flex, "text": text}]

    def total(self) -> int:
        return sum(leg.reps for leg in self.legs.values())

    def _completed(self) -> bool:
        return self.total() >= self.p["target_reps"]

    def status(self, t):
        d = super().status(t)
        d.update(reps=self.total(), target_reps=self.p["target_reps"], hold_target_s=self.p["hold_s"],
                 by_side={s: leg.reps for s, leg in self.legs.items()})
        holding = [t - leg.up_t for leg in self.legs.values() if leg.st == "up" and leg.drop_t is None]
        d["holding_s"] = round(max(holding), 1) if holding else None
        if self.phase == "running":
            d.update(_knee_live(self.legs, t, self.p["hold_s"]))
        return d

    def _metrics(self) -> dict:
        hs = [h["hold_s"] for h in self.holds]
        flex = {s: max((h["flexion"] for h in self.holds if h["side"] == s and h["flexion"] is not None), default=None)
                for s in self.legs}
        return {"reps": self.total(), "target_reps": self.p["target_reps"],
                "by_side": {s: leg.reps for s, leg in self.legs.items()},
                "hold_target_s": self.p["hold_s"], "holds": self.holds,
                "hold_mean_s": round(statistics.mean(hs), 1) if hs else None,
                "reps_hold_ok": sum(h["ok"] for h in self.holds),
                "flexion_max_experimental": flex, "unseen": self.unseen, "stood_up": self.stood,
                "base_flexion": {s: (round(180 - b) if b is not None else None) for s, b in self.base.items()}}


# =====================================================================
# 站立平衡（并脚 / 半前后脚 / 前后脚 / 单脚站）：只记保持秒数；扶没扶由本人自报
# =====================================================================

VARIANT_TEXT = {"together": "并脚站", "semi_tandem": "半前后脚站", "tandem": "前后脚站", "single": "单脚站"}


class BalanceHold(Exercise):
    kind, name = "balance", "站立平衡"
    seated = False
    DEFAULTS = {"variant": "together", "target_s": 10.0, "attempts": 3}
    MOVE = 0.12  # 任一脚踝离开起始位置超过 0.12 个腿长：脚挪了，这次结束
    LIFT = 0.06  # 单脚站：两脚踝高度差 ≥ 0.06 个腿长算抬起
    DOWN = 0.03  # 高度差回到 0.03 以内算放下
    LOST_S = 0.5  # 看不到超过这么久，这次结束（记「没看全」）
    PROMPTS = {
        "prep": "请{vt}，扶好台面或椅背，准备好后保持不动",
        "running": "保持住，尽量站稳（目标 {target_s:g} 秒）",
        "between": "放松一下，准备好后再{vt}",
        "done": "完成",
        "aborted": "已取消",
    }
    limit_s = 600.0

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.attempts: list[dict] = []
        self.hold_t: float | None = None
        self.anchor: dict[str, np.ndarray] = {}
        self.leg = 1.0
        self.last_seen = 0.0

    def prompt(self) -> str:
        return self.PROMPTS.get(self.phase, "").format(vt=VARIANT_TEXT.get(self.p["variant"], "站好"), **self.p)

    def _ready(self, t: float, o: Obs) -> bool:
        if not (o.observable and o.body and o.ps and o.ps.raw == "stand" and t - o.ps.since >= 0.5):
            return False
        if self.p["variant"] == "single":
            return self._lift(o.body) is not None and self._lift(o.body) >= self.LIFT
        return True

    def _lift(self, b: Body) -> float | None:
        """两脚踝的高度差（腿长的比例）；有一只看不到返回 None。"""
        if not (b.ok(SIDE["left"]["ankle"]) and b.ok(SIDE["right"]["ankle"])):
            return None
        leg = b.leg_px("left") or b.leg_px("right")
        if not leg:
            return None
        return abs(b.px(SIDE["left"]["ankle"])[1] - b.px(SIDE["right"]["ankle"])[1]) / leg

    def _begin_hold(self, t: float, b: Body) -> None:
        """记下脚的起始位置。单脚站只看支撑脚（抬起的那只本来就会动）。"""
        self.hold_t, self.last_seen = t, t
        feet = ("left", "right")
        if self.p["variant"] == "single":
            lifted = self._lifted_side(b)
            feet = tuple(s for s in feet if s != lifted)
        self.anchor = {s: b.px(SIDE[s]["ankle"]) for s in feet if b.ok(SIDE[s]["ankle"])}
        self.leg = b.leg_px("left") or b.leg_px("right") or 1.0

    def _prep(self, t, o):
        if self._ready(t, o):
            self._begin_hold(t, o.body)
            return [self._goto("running", t)]
        return []

    def _run(self, t, o):
        if self.phase == "between":
            if t - self.phase_t >= 1.5 and self._ready(t, o):
                self._begin_hold(t, o.body)
                return [self._goto("running", t)]
            return []
        # running：一次保持
        hold = t - self.hold_t
        ended = None
        if not o.observable or o.body is None:
            if t - self.last_seen > self.LOST_S:
                ended = ("unseen", self.last_seen - self.hold_t)
        else:
            self.last_seen = t
            b = o.body
            if o.ps and o.ps.sit_stable:
                ended = ("sat", hold)
            if self.p["variant"] == "single":
                lift = self._lift(b)
                if lift is not None and lift < self.DOWN:
                    ended = ended or ("foot_down", hold)
            for s, p0 in self.anchor.items():
                if b.ok(SIDE[s]["ankle"]) and np.linalg.norm(b.px(SIDE[s]["ankle"]) - p0) / self.leg > self.MOVE:
                    ended = ended or ("moved", hold)
            if hold >= self.p["target_s"]:
                ended = ended or ("target", float(self.p["target_s"]))
        if not ended:
            return []
        return self._end_attempt(t, *ended)

    def _lifted_side(self, b: Body) -> str | None:
        if not (b.ok(SIDE["left"]["ankle"]) and b.ok(SIDE["right"]["ankle"])):
            return None
        return "left" if b.px(SIDE["left"]["ankle"])[1] < b.px(SIDE["right"]["ankle"])[1] else "right"

    ENDED_TEXT = {"target": "达到目标", "moved": "脚挪动了", "foot_down": "脚放下了", "sat": "坐下了",
                  "unseen": "中途没看到，只记到看见的部分"}

    def _end_attempt(self, t: float, why: str, hold: float) -> list[dict]:
        hold = max(0.0, hold)
        a = {"n": len(self.attempts) + 1, "hold_s": round(hold, 1), "ended_by": why, "support": None,
             "complete": why != "unseen"}
        self.attempts.append(a)
        ev = {"kind": "ex_attempt", "exercise": self.kind, **a, "ask_support": True,
              "text": f"第 {a['n']} 次{VARIANT_TEXT.get(self.p['variant'], '')}：保持 {hold:.1f} 秒（{self.ENDED_TEXT[why]}）"}
        out = [ev]
        if why == "target" or len(self.attempts) >= self.p["attempts"]:
            out.append(self._finish(t, completed=why == "target" or len(self.attempts) >= self.p["attempts"]))
        else:
            self.phase, self.phase_t = "between", t
            out.append(self._phase_event())
        return out

    def self_report(self, support: str) -> dict | None:
        """本人自报这次扶没扶（yes / no）。改的是最近一次。"""
        if not self.attempts:
            return None
        self.attempts[-1]["support"] = support
        return self.attempts[-1]

    def status(self, t):
        d = super().status(t)
        d.update(variant=self.p["variant"], variant_text=VARIANT_TEXT.get(self.p["variant"]),
                 target_s=self.p["target_s"], attempts=len(self.attempts), max_attempts=self.p["attempts"],
                 holding_s=round(t - self.hold_t, 1) if self.phase == "running" and self.hold_t else None,
                 best_s=self._best())
        return d

    def _best(self) -> float | None:
        hs = [a["hold_s"] for a in self.attempts if a["complete"]]
        return max(hs) if hs else None

    def _metrics(self) -> dict:
        return {"reps": len(self.attempts), "best_s": self._best(), "target_s": self.p["target_s"],
                "variant": self.p["variant"], "attempts": self.attempts}

    def _finish(self, t, completed):
        r = super()._finish(t, completed)
        if self.attempts:
            r["attempts"] = self.attempts  # 同一个列表：结果出来后自报的扶没扶也能补进去
        return r


# =====================================================================
# 2 分钟原地抬膝：膝盖抬到「髋和膝的中点」高度记一次；按标准记法，成绩为右膝次数
# =====================================================================


class MarchInPlace(Exercise):
    kind, name = "march2min", "2 分钟原地抬膝"
    seated = False
    DEFAULTS = {"duration_s": 120.0}
    COUNTDOWN_S = 3.0
    PROMPTS = {
        "prep": "请站直，扶好台面，先站稳 1 秒（量一下抬膝的目标高度）",
        "countdown": "准备：3、2、1",
        "running": "原地抬膝：膝盖抬到髋和膝的中间高度，左右交替，尽量多做",
        "done": "完成",
        "aborted": "已取消",
    }

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.limit_s = float(self.p["duration_s"])
        self.target: dict[str, float] = {}
        self.reset: dict[str, float] = {}
        self.state = {"left": "down", "right": "down"}
        self.counts = {"left": 0, "right": 0}
        self.samples: list[Body] = []

    def _prep(self, t, o):
        if not (o.observable and o.body and o.ps and o.ps.raw == "stand"):
            self.samples = []
            return []
        self.samples.append(o.body)
        if len(self.samples) < 12:
            return []
        for s in ("left", "right"):
            ks = [b.px(SIDE[s]["knee"])[1] for b in self.samples if b.ok(SIDE[s]["knee"], SIDE[s]["hip"])]
            hs = [b.px(SIDE[s]["hip"])[1] for b in self.samples if b.ok(SIDE[s]["knee"], SIDE[s]["hip"])]
            if len(ks) >= 6:
                k, h = statistics.median(ks), statistics.median(hs)
                self.target[s] = (k + h) / 2  # 画面 y 向下：比它小就是抬过了
                self.reset[s] = self.target[s] + 0.4 * (k - self.target[s])
        if not self.target:
            self.samples = []
            return self._note("看不清膝盖，请侧身站到画面中间")
        return [self._goto("countdown", t)]

    def _run(self, t, o):
        if self.phase == "countdown":
            if t - self.phase_t >= self.COUNTDOWN_S:
                return [self._goto("running", t)]
            return []
        out: list[dict] = []
        if self._left_frame(t):
            self.invalid.add("LEFT_FRAME")
        if o.observable and o.ps and o.ps.sit_stable:
            self.invalid.add("SAT_DOWN")
        if o.observable and o.body:
            for s in self.target:
                if not o.body.ok(SIDE[s]["knee"]):
                    continue
                y = o.body.px(SIDE[s]["knee"])[1]
                if self.state[s] == "down" and y < self.target[s]:
                    self.state[s] = "up"
                    self.counts[s] += 1
                    out.append({"kind": "ex_rep", "exercise": self.kind, "counted": True, "side": s,
                                "reps": self.counts["right"], "by_side": dict(self.counts)})
                elif self.state[s] == "up" and y > self.reset[s]:
                    self.state[s] = "down"
        return out

    def status(self, t):
        d = super().status(t)
        d.update(reps=self.counts["right"], by_side=dict(self.counts), duration_s=self.p["duration_s"])
        if self.phase == "running":
            d["remaining_s"] = max(0.0, round(self.p["duration_s"] - (t - self.phase_t), 1))
        if self.phase == "countdown":
            d["remaining_s"] = max(0.0, round(self.COUNTDOWN_S - (t - self.phase_t), 1))
        return d

    def _metrics(self) -> dict:
        return {"reps": self.counts["right"], "by_side": dict(self.counts), "duration_s": self.p["duration_s"],
                "measured_sides": sorted(self.target), "scoring": "右膝达到目标高度的次数"}


# =====================================================================
# 扶台面转移重心：正面机位，骨盆移到一侧脚上方记一次；不给晃动幅度
# =====================================================================


class WeightShift(Exercise):
    kind, name = "weight_shift", "扶台面转移重心"
    seated = False
    view = "front"
    DEFAULTS = {"target_reps": 10, "duration_s": 120.0}
    FRONT_MIN = 0.45  # 肩宽 ÷ 躯干长低于它，说明不是正面，左右移动看不出来
    SIDE_TH = 0.5  # 骨盆中心偏到半个站距的 50% 算移到一侧
    CENTER_TH = 0.25
    PROMPTS = {
        "prep": "请面向摄像头站好，双手扶住台面，两脚分开与肩同宽",
        "running": "慢慢把重心移到一侧，再移到另一侧，脚不要离地",
        "done": "完成",
        "aborted": "已取消",
    }

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.limit_s = float(self.p["duration_s"])
        self.state = "center"
        self.counts = {"left": 0, "right": 0}

    def _frontal(self, b: Body | None) -> bool:
        f = b.frontal() if b else None
        return f is not None and f >= self.FRONT_MIN

    def _prep(self, t, o):
        if not (o.observable and o.body and o.ps and o.ps.raw == "stand" and t - o.ps.since >= 0.5):
            return []
        if not self._frontal(o.body):
            return self._note("需要正面机位：请让摄像头正对着人（正面训练位）")
        return [self._goto("running", t)]

    def _shift(self, b: Body) -> float | None:
        la, ra = SIDE["left"]["ankle"], SIDE["right"]["ankle"]
        hc = b.hip_center()
        if hc is None or not (b.ok(la) and b.ok(ra)):
            return None
        mid = (b.px(la)[0] + b.px(ra)[0]) / 2
        half = abs(b.px(la)[0] - b.px(ra)[0]) / 2
        if half < 0.02 * b.w:
            return None
        return (hc[0] - mid) / half

    def _run(self, t, o):
        out: list[dict] = []
        if self._left_frame(t):
            self.invalid.add("LEFT_FRAME")
        if not (o.observable and o.body):
            return out
        if not self._frontal(o.body):
            self.invalid.add("NOT_FRONTAL")
            return out + self._note("机位不是正面，左右移动看不准")
        x = self._shift(o.body)
        if x is None:
            return out
        if self.state == "center" and abs(x) >= self.SIDE_TH:
            # 正面机位里画面左边是本人的右边
            side = "right" if x < 0 else "left"
            self.state = side
            self.counts[side] += 1
            n = self.total()
            out.append({"kind": "ex_rep", "exercise": self.kind, "counted": True, "side": side, "reps": n,
                        "text": f"第 {n} 次（移到{'右' if side == 'right' else '左'}侧）"})
        elif self.state != "center" and abs(x) <= self.CENTER_TH:
            self.state = "center"
        if self.total() >= self.p["target_reps"]:
            out.append(self._finish(t, completed=True))
        return out

    def total(self) -> int:
        return sum(self.counts.values())

    def _completed(self) -> bool:
        return self.total() >= self.p["target_reps"]

    def status(self, t):
        d = super().status(t)
        d.update(reps=self.total(), target_reps=self.p["target_reps"], by_side=dict(self.counts))
        if self.phase == "running":
            d["remaining_s"] = max(0.0, round(self.p["duration_s"] - (t - self.phase_t), 1))
        return d

    def _metrics(self) -> dict:
        return {"reps": self.total(), "target_reps": self.p["target_reps"], "by_side": dict(self.counts),
                "duration_s": self.p["duration_s"]}


EXERCISES: dict[str, type[Exercise]] = {c.kind: c for c in (KneeExtension, KneeFlexion, BalanceHold,
                                                             MarchInPlace, WeightShift)}

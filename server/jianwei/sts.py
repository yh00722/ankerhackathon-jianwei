"""30 秒坐站（30-second chair stand）。

流程：准备坐好 → 站直（取站姿基线）→ 坐下 → 倒数 3 秒 → 计时 30 秒。
这个机位标定过（calib.py）时，直接用标定的坐、站基线：坐好 → 倒数 → 计时，省掉一站一坐。
计数：进度 p 从 ≤ 0.3 升到 ≥ 0.85 记 1 次；到 30 秒时已起身过半（p > 0.5）再加 1 次（与 CDC STEADI 说明一致）。
无效：中途离开画面 > 1 秒、有他人在旁、镜头转动。结果保留，但不进入趋势。
双手：标准做法是双手交叉放在胸前。起身过程中手腕低于「髋往上 1/4 躯干」那条线（撑膝、扶扶手），
这一次记「双手没放在胸前」；有这种情况的结果，条件记为「用了手」，只和同样用了手的记录比较（不判对错）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

PROMPTS = {
    "prep_sit": "请在椅子上坐好",
    "prep_stand": "请站直，保持 1 秒",
    "prep_sit2": "请坐下，准备开始",
    "countdown": "准备：3、2、1",
    "running": "开始！尽量多次站起、坐下",
    "done": "完成",
    "aborted": "已取消",
}


@dataclass
class StsConfig:
    run_s: float = 30.0
    countdown_s: float = 3.0
    hold_s: float = 0.6  # 准备阶段坐、站各需稳定多久（在姿态「稳定」之外再加的时长）
    up: float = 0.85
    down: float = 0.3
    half: float = 0.5
    leave_s: float = 1.0
    prep_timeout_s: float = 90.0
    min_amp: float = 0.2


@dataclass
class StsTest:
    cfg: StsConfig = field(default_factory=StsConfig)

    def __post_init__(self) -> None:
        self.phase = "idle"
        self.reps = 0
        self.invalid: set[str] = set()
        self.baseline: tuple[float, float] | None = None
        self.arms_reps = 0

    @property
    def active(self) -> bool:
        return self.phase not in ("idle", "done", "aborted")

    def start(self, t: float, baseline: tuple[float, float] | None = None) -> dict:
        """baseline：标定得到的（坐 f1, 站 f1）；有就跳过站一次取基线。"""
        self.phase, self.phase_t = "prep_sit", t
        self.reps, self.invalid = 0, set()
        self.sit_b = self.stand_b = None
        self.baseline = baseline
        self.arms_reps = 0
        self.rep_arms = False
        self.armed, self.last_p = True, 0.0
        self.unobs_since: float | None = None
        self.started_at = t
        return self._phase_event()

    def cancel(self, t: float) -> dict:
        self.phase, self.phase_t = "aborted", t
        return self._phase_event()

    def status(self, t: float) -> dict:
        d = {"phase": self.phase, "prompt": PROMPTS.get(self.phase, ""), "reps": self.reps}
        if self.phase == "running":
            d["remaining_s"] = max(0.0, round(self.cfg.run_s - (t - self.phase_t), 1))
        if self.phase == "countdown":
            d["remaining_s"] = max(0.0, round(self.cfg.countdown_s - (t - self.phase_t), 1))
        return d

    def update(self, t: float, observable: bool, reason: str | None, ps, other_near: bool,
               f1_window=None, body=None) -> list[dict]:
        """f1_window(t0, t1) -> 中位数，用于取基线（由调用方提供历史）。body：目标的关键点（检查双手位置）。"""
        if not self.active:
            return []
        out: list[dict] = []
        c = self.cfg
        if self.phase.startswith("prep") and t - self.phase_t > c.prep_timeout_s:
            return [self.cancel(t)]

        if self.phase == "prep_sit":
            if observable and ps.sit_stable and t - ps.since >= c.hold_s:
                if self.baseline:
                    self.sit_b, self.stand_b = self.baseline
                    out.append(self._goto("countdown", t))
                else:
                    self.sit_b = f1_window(ps.since, t) if f1_window else ps.f1
                    out.append(self._goto("prep_stand", t))
        elif self.phase == "prep_stand":
            if observable and ps.stand_stable and t - ps.since >= c.hold_s:
                self.stand_b = f1_window(ps.since, t) if f1_window else ps.f1
                if self.sit_b is None or self.stand_b - self.sit_b < c.min_amp:
                    self.sit_b, self.stand_b = 0.3, 0.9  # 基线不可靠时退回默认值
                    self.invalid.add("CALIBRATION")
                out.append(self._goto("prep_sit2", t))
        elif self.phase == "prep_sit2":
            if observable and ps.sit_stable:
                out.append(self._goto("countdown", t))
        elif self.phase == "countdown":
            if t - self.phase_t >= c.countdown_s:
                self.armed = True
                out.append(self._goto("running", t))
        elif self.phase == "running":
            out += self._running(t, observable, reason, ps, other_near, body)
        return out

    # ---- 内部 ----

    def _running(self, t, observable, reason, ps, other_near, body=None) -> list[dict]:
        c, out = self.cfg, []
        if other_near:
            self.invalid.add("OTHER_PERSON")
        if not observable:
            if reason == "CAMERA_MOVING":
                self.invalid.add("CAMERA_MOVING")
            if self.unobs_since is None:
                self.unobs_since = t
            elif t - self.unobs_since > c.leave_s:
                self.invalid.add("LEFT_FRAME")
        else:
            self.unobs_since = None
            if ps.f1 is not None:
                p = (ps.f1 - self.sit_b) / (self.stand_b - self.sit_b)
                self.last_p = p
                # 只看起身这一段（重新起算后 p 先降到 0.3 以下，再升过 0.3 才是在起身）
                if self.armed and body is not None and c.down < p < c.up and body.wrists_on_chest() is False:
                    self.rep_arms = True  # 起身过程中双手不在胸前
                if self.armed and p >= c.up:
                    self.reps += 1
                    self.armed = False
                    arms = self.rep_arms
                    self.arms_reps += int(arms)
                    self.rep_arms = False
                    out.append({"kind": "sts_rep", "reps": self.reps, "arms": arms})
                elif not self.armed and p <= c.down:
                    self.armed = True
                    self.rep_arms = False
        if t - self.phase_t >= c.run_s:
            if self.armed and self.last_p > c.half:
                self.reps += 1
            self.phase = "done"
            out.append(self.result(t))
        return out

    def result(self, t: float) -> dict:
        return {
            "kind": "sts_result",
            "reps": self.reps,
            "valid": not (self.invalid - {"CALIBRATION"}),
            "invalid_reasons": sorted(self.invalid),
            "sit_base": None if self.sit_b is None else round(self.sit_b, 3),
            "stand_base": None if self.stand_b is None else round(self.stand_b, 3),
            "baseline": "calibrated" if self.baseline else "measured",
            "arms_reps": self.arms_reps,
        }

    def _goto(self, phase: str, t: float) -> dict:
        self.phase, self.phase_t = phase, t
        return self._phase_event()

    def _phase_event(self) -> dict:
        return {"kind": "sts_phase", "phase": self.phase, "prompt": PROMPTS.get(self.phase, "")}

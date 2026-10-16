"""分析引擎：把逐帧处理、会话、观测区间、起身识别、30 秒坐站、训练动作、久坐提醒和事件入库串起来。

线程模型：process() 在线程池里跑（姿态推理较慢），tick() 在事件循环里每 125 毫秒跑一次；
两者都通过 self.lock 修改状态机。
"""

from __future__ import annotations

import threading
import time
from collections import deque

from . import features as F
from .bridge import Bridge
from .calib import Calibrator, thresholds_from
from .config import ROOT, Settings
from .debugrec import DebugRecorder
from .exercises import EXERCISES, INVALID_TEXT, Exercise, Obs
from .kin import Body
from .motion import GlobalMotion
from .posture import PostureClassifier
from .rise import RiseDetector
from .store import DEFAULT_CONDITIONS, Store
from .sts import StsTest
from .tracker import TargetTracker

REASON_TEXT = {
    "STREAM_DOWN": "摄像头暂时断开",
    "CAMERA_MOVING": "摄像头在转动",
    "NO_PERSON": "画面里没有人",
    "AMBIGUOUS": "旁边有人，分不清是谁的动作",
    "LOW_QUALITY": "看不清",
    "INVERTED": "画面是倒的（请在控制台旋转画面）",
    "GAP": "中间没看到",
}


def derotate(xy, rotate: int):
    """把旋转后图像里的归一化坐标换回原始画面坐标（控制台在原始画面上画骨架）。"""
    x, y = xy
    if rotate == 180:
        return 1 - x, 1 - y
    if rotate == 90:  # 顺时针转过 90°
        return y, 1 - x
    if rotate == 270:
        return 1 - y, x
    return x, y


class Engine:
    def __init__(self, settings: Settings, store: Store, bridge: Bridge, estimator=None):
        self.s, self.store, self.bridge = settings, store, bridge
        self.est = estimator
        self.training_est_factory = None  # 训练模式用的精度更高的模型（由 app 设置，第一次用时才加载）
        self._training_est = None
        self.training_model_name = "heavy"
        self.model = "full"
        self.lock = threading.Lock()
        self.posture, self.rise, self.tracker, self.sts = PostureClassifier(), RiseDetector(), TargetTracker(), StsTest()
        self.motion, self.calib = GlobalMotion(), Calibrator()
        # 端到端画面延迟：云台指令发出 → 画面里第一次看到整体位移（毫秒），按指令编号记
        self.motion_lat: dict[int, float] = {}
        self.inverted_hist: deque[bool] = deque(maxlen=15)  # 最近 15 帧目标是不是倒着的
        self.rec = DebugRecorder(ROOT / "data" / "debug")
        self.session: dict | None = None
        self.obs: list | None = None  # 当前观测区间 [observable, reason, t0]
        self.f1_hist: deque[tuple[float, float]] = deque()
        self.recv: deque[float] = deque(maxlen=90)
        self.done: deque[float] = deque(maxlen=90)
        self.last_frame_mono: float | None = None
        self.last_t = 0.0  # 上一帧的拍摄时刻：保证送进状态机的时间单调
        self.frame_src: str | None = None
        self.latency_ms: float | None = None
        self.infer_ms: float | None = None
        self.view: dict = {"observable": False, "reason": "STREAM_DOWN", "posture": None, "persons": 0,
                           "other_near": False}
        self.events: deque[dict] = deque(maxlen=30)
        self.last_sts: dict | None = None
        self.ex: Exercise | None = None  # 当前训练动作（同一时间只有一个，与 30 秒坐站、标定互斥）
        self.ex_result: dict | None = None  # 最近一次训练动作的结果（站立平衡结束后还能补「扶没扶」）
        self.reminder: dict | None = None  # 当前久坐提醒
        self.reminded_seat: float | None = None  # 已经提醒过的那段坐（坐下时刻）
        self.plan = store.plan() if hasattr(store, "plan") else []
        self.on_event = None  # 由 app 设置：有新事件时推送给页面

    # ---- 时间 ----

    @staticmethod
    def wall(t_mono: float) -> float:
        return time.time() - (time.monotonic() - t_mono)

    # ---- 会话 ----

    def start_session(self, mode: str, label: str) -> dict:
        with self.lock:
            if self.session and self.session["status"] in ("starting", "observing", "paused"):
                self._end_locked()
            sid = self.store.add_session(mode, label, time.time())
            self.bridge.enqueue("SET_TRACKING", on=False)  # 先关 AI 追踪：只看授权的预置位
            cmd = self.bridge.enqueue("GOTO_PRESET", index=self.s.presets[mode])
            self.session = {"id": sid, "mode": mode, "label": label, "status": "starting", "cmd_id": cmd["id"],
                            "started_at": time.time(), "preset_ok": None, "_start_mono": time.monotonic()}
            self._load_thresholds()
            self._reset_trackers()
            self._push({"kind": "session", "text": f"开始{'训练' if mode == 'training' else '观察'}，摄像头正在转向"
                                                    f"{'训练位' if mode == 'training' else '日常区'}"})
            return self.session_public()

    def pause(self) -> dict | None:
        with self.lock:
            if not self.session or self.session["status"] not in ("starting", "observing"):
                return self.session_public()
            self._close_interval(time.monotonic())
            self.bridge.enqueue("SET_TRACKING", on=False)  # 追踪开着的话，转向墙面后又会跟着人转回来
            cmd = self.bridge.enqueue("GOTO_PRESET", index=self.s.presets["privacy"])
            self.session.update(status="paused", cmd_id=cmd["id"], preset_ok=None)
            self.store.set_session(self.session["id"], status="paused")
            if self.sts.active:
                self.sts.cancel(time.monotonic())
            if self.calib.active:
                self.calib.cancel(time.monotonic())
            self._cancel_ex_locked()
            self._end_reminder("session")
            self._push({"kind": "session", "text": "已暂停，摄像头正在转向墙面"})
            return self.session_public()

    def resume(self) -> dict | None:
        with self.lock:
            if not self.session or self.session["status"] != "paused":
                return self.session_public()
            self.bridge.enqueue("SET_TRACKING", on=False)
            cmd = self.bridge.enqueue("GOTO_PRESET", index=self.s.presets[self.session["mode"]])
            self.session.update(status="starting", cmd_id=cmd["id"], preset_ok=None, _start_mono=time.monotonic())
            self._load_thresholds()
            self._reset_trackers()
            self._push({"kind": "session", "text": "继续观察，摄像头正在转回来"})
            return self.session_public()

    def stop(self) -> dict | None:
        with self.lock:
            if not self.session:
                return None
            self._end_locked()
            return self.session_public()

    def _end_locked(self) -> None:
        self._close_interval(time.monotonic())
        if self.sts.active:
            self.sts.cancel(time.monotonic())
        if self.calib.active:
            self.calib.cancel(time.monotonic())
        self._cancel_ex_locked()
        self._end_reminder("session")
        self.bridge.enqueue("SET_TRACKING", on=False)
        self.bridge.enqueue("GOTO_PRESET", index=self.s.presets["privacy"])
        self.session.update(status="ended")
        self.store.set_session(self.session["id"], status="ended", ended_at=time.time())
        self._push({"kind": "session", "text": "已结束，摄像头已转向墙面"})

    def _reset_trackers(self) -> None:
        self.posture.reset()
        self.rise.reset()
        self.tracker.reset()
        self.obs = None

    def start_sts(self) -> dict:
        with self.lock:
            if not self.session or self.session["mode"] != "training" or self.session["status"] != "observing":
                return {"error": "请先开始训练，并等摄像头转到训练位"}
            if self.calib.active:
                return {"error": "正在标定，请等标定完成"}
            if self.ex and self.ex.active:
                return {"error": f"{self.ex.name}进行中，请先结束"}
            item = self._plan_item("sts30")
            if item and not item["enabled"]:
                return {"error": "康复师的训练计划里没有打开 30 秒坐站"}
            c = self.store.calibration(self.s.presets["training"])
            return self.sts.start(time.monotonic(), (c["sit_f1"], c["stand_f1"]) if c else None)

    # ---- 训练动作 ----

    def _plan_item(self, kind: str) -> dict | None:
        return next((r for r in self.plan if r["kind"] == kind), None)

    def reload_plan(self) -> None:
        with self.lock:
            self.plan = self.store.plan()

    def start_exercise(self, kind: str) -> dict:
        with self.lock:
            cls = EXERCISES.get(kind)
            if cls is None:
                return {"error": f"不认识的动作：{kind}"}
            if not self.session or self.session["mode"] != "training" or self.session["status"] != "observing":
                return {"error": "请先开始训练，并等摄像头转到训练位"}
            if self.calib.active or self.sts.active or (self.ex and self.ex.active):
                return {"error": "另一个训练或标定正在进行，请先结束"}
            item = self._plan_item(kind)
            if not item or not item["enabled"]:
                return {"error": f"康复师的训练计划里没有打开「{cls.name}」"}
            self.ex = cls(item["params"])
            self.ex_result = None
            now = time.monotonic()
            self._push({**self.ex.start(now), "text": f"开始{self.ex.name}：{self.ex.prompt()}"}, store_log=False)
            if self.ex.view == "front" and "front" in self.s.presets:
                self.bridge.enqueue("GOTO_PRESET", index=self.s.presets["front"])  # 转到正面训练位
            return self.ex.status(now)

    def cancel_exercise(self) -> dict:
        with self.lock:
            self._cancel_ex_locked()
            return self.ex.status(time.monotonic()) if self.ex else {}

    def _cancel_ex_locked(self) -> None:
        if self.ex and self.ex.active:
            for e in self.ex.cancel(time.monotonic()):
                self._handle(e)

    def exercise_self_report(self, support: str) -> dict | None:
        """站立平衡：本人自报刚才扶没扶。结果已入库的，同时更新库里的明细。"""
        with self.lock:
            if not self.ex or not hasattr(self.ex, "self_report"):
                return None
            a = self.ex.self_report(support)
            if a and self.ex_result and self.ex_result.get("id"):
                self.store.set_training_details(self.ex_result["id"], self._ex_details(self.ex_result))
            return a

    @staticmethod
    def _ex_details(e: dict) -> dict:
        return {k: v for k, v in e.items() if k not in ("kind", "valid", "invalid_reasons", "params", "id", "at", "text")}

    def _ex_text(self, e: dict) -> str:
        k, n = e["exercise"], e.get("reps", 0)
        if k in ("knee_ext", "knee_flex"):
            s = f"{e['name']}：{n} 次"
            if e.get("hold_mean_s") is not None:
                s += f"，平均保持 {e['hold_mean_s']:.1f} 秒（{e['reps_hold_ok']} 次达到 {e['hold_target_s']:g} 秒）"
            if e.get("unseen"):
                s += f"；{e['unseen']} 次没看全，没有计入"
        elif k == "balance":
            best = e.get("best_s")
            s = f"{e['name']}：最长保持 {best:.1f} 秒（目标 {e['target_s']:g} 秒，共 {n} 次）" if best is not None \
                else f"{e['name']}：没有完整看到的保持"
        elif k == "march2min":
            s = f"{e['name']}：右膝 {n} 次（左膝 {e['by_side']['left']} 次）"
        else:
            s = f"{e['name']}：{n} 次（左 {e['by_side']['left']}、右 {e['by_side']['right']}）"
        if not e["completed"]:
            s += "，提前结束"
        if not e["valid"]:
            s += "（本次无效，不进入趋势：" + "、".join(INVALID_TEXT.get(r, r) for r in e["invalid_reasons"]) + "）"
        return s

    # ---- 久坐提醒 ----

    def _check_reminder(self, now: float) -> None:
        """日常观察里连续坐够阈值提醒一次；站起来（或这段坐结束）提醒就结束。调用方持有 self.lock。"""
        s = self.session
        if not s or s["status"] != "observing" or s["mode"] != "daily":
            return
        r = self.reminder
        seat = self.rise.seat_start
        if r:
            if self.rise.state == "standing" or (seat is not None and seat != r["seat_start"]):
                self._end_reminder("stood_up")
            elif r.get("snooze_until") and now >= r["snooze_until"]:
                r["snooze_until"] = None
                r["times"] += 1
                self._push({"kind": "reminder", "id": r["id"], "again": True, "sit_s": round(now - r["seat_start"]),
                            "text": f"已经连续坐了 {_fmt_min(now - r['seat_start'])}，起来活动一下吧"})
            return
        sit = self.rise.current_sit_s(now)
        th = self.s.sedentary_demo_s if self.s.demo_fast else self.s.sedentary_s
        if sit is not None and sit >= th and seat != self.reminded_seat:
            self.reminded_seat = seat
            rid = self.store.add_reminder(s["id"], time.time(), sit)
            self.reminder = {"id": rid, "seat_start": seat, "snooze_until": None, "times": 1, "at": time.time()}
            self._push({"kind": "reminder", "id": rid, "sit_s": round(sit),
                        "text": f"已经连续坐了 {_fmt_min(sit)}，起来活动一下吧"})

    def respond_reminder(self, rid: int, response: str) -> dict | None:
        with self.lock:
            r = self.reminder
            if not r or r["id"] != rid:
                return None
            self.store.respond_reminder(rid, response)
            if response == "later":
                r["snooze_until"] = time.monotonic() + (self.s.snooze_demo_s if self.s.demo_fast else self.s.snooze_s)
            r["response"] = response
            return {k: v for k, v in r.items() if k != "seat_start"}

    def _end_reminder(self, by: str) -> None:
        r = self.reminder
        if not r:
            return
        self.reminder = None
        self.store.end_reminder(r["id"], by)
        text = "站起来了，提醒结束" if by == "stood_up" else "提醒结束"
        self._push({"kind": "reminder_end", "id": r["id"], "by": by, "text": text}, store_log=by == "stood_up")

    # ---- 标定（按预置位） ----

    def _preset(self) -> int | None:
        return self.s.presets[self.session["mode"]] if self.session else None

    def _load_thresholds(self) -> None:
        """按当前会话的预置位载入标定阈值；没标定过用默认阈值。"""
        preset = self._preset()
        self.posture.set_thresholds(thresholds_from(self.store.calibration(preset) if preset else None))

    def start_calibration(self) -> dict:
        with self.lock:
            if not self.session or self.session["status"] != "observing":
                return {"error": "请先开始观察或训练，并等摄像头转到位"}
            if self.sts.active:
                return {"error": "30 秒坐站进行中，请先结束"}
            e = self.calib.start(time.monotonic(), self._preset())
            self._push({**e, "text": "开始标定：" + e["prompt"]}, store_log=False)
            return self.calib.status(time.monotonic())

    def cancel_calibration(self) -> dict:
        with self.lock:
            if self.calib.active:
                self._push({**self.calib.cancel(time.monotonic()), "text": "已取消标定"}, store_log=False)
            return self.calib.status(time.monotonic())

    def reset_calibration(self, preset: int) -> None:
        with self.lock:
            self.store.delete_calibration(preset)
            if self.session and self._preset() == preset:
                self._load_thresholds()
                self._reset_trackers()

    def calibration_info(self) -> dict:
        th = self.posture.th
        return {"calibrated": th.calibrated, "sit_f1": round(th.sit_f1, 3), "stand_f1": round(th.stand_f1, 3),
                "sit_hip": round(th.sit_hip, 1), "stand_hip": round(th.stand_hip, 1)}

    # ---- 镜头转动 ----

    def camera_moving(self, now: float | None = None) -> bool:
        """镜头是否在转动：结合我们下的云台指令和画面整体位移（见 config.py 的说明）。"""
        now = now or time.monotonic()
        c = self.bridge.last_move()
        if c is None or now - c["_mono"] > self.s.move_timeout_s + self.s.move_cap_s:
            return self.motion.sustained(now)  # 不是我们下的指令：画面持续整体位移也算（手动转、自动追踪）
        if c["ack_mono"] is None:
            return now - c["_mono"] < self.s.move_timeout_s or self.motion.sustained(now)
        since_ack = now - c["ack_mono"]
        if since_ack >= self.s.move_cap_s:
            return self.motion.sustained(now)
        if self.motion.moved_since(c["_mono"]):
            return self.motion.moving(now)  # 画面已经开始动：等它停下
        return since_ack < self.s.move_wait_s  # 画面还没动：再等一会儿，等不到说明本来就在这个位置

    def cancel_sts(self) -> dict:
        with self.lock:
            return self.sts.cancel(time.monotonic()) if self.sts.active else self.sts.status(time.monotonic())

    def session_public(self) -> dict | None:
        if not self.session:
            return None
        return {k: v for k, v in self.session.items() if not k.startswith("_")}

    # ---- 逐帧处理（线程池） ----

    def process(self, jpeg: bytes, t: float, cap_ms: float | None) -> None:
        from .pose import decode

        t0 = time.monotonic()
        rgb = decode(jpeg, self.s.rotate, self.s.infer_width)
        if rgb is None:
            return
        h, w = rgb.shape[:2]
        est = self._estimator()
        poses = est.detect(rgb, int(t * 1000)) if est else []
        feats = [F.compute(p, w, h) for p in poses]
        boxes = [F.bbox_px(p, w, h) for p in poses]
        with self.lock:
            t = max(t, self.last_t + 1e-3)  # 断流心跳用的是当前时刻，恢复后的帧不能比它早
            self.last_t = t
            self.motion.update(t, rgb, boxes)
            if self.motion.last_motion_t == t:
                c = self.bridge.last_move()
                # 只统计设备确认执行了的指令（ACK 在 250 ms 内，画面要 1 秒左右才动，此时一般已确认）
                if c and c["state"] == "done" and c["id"] not in self.motion_lat and 0 < t - c["_mono"] < 10:
                    self.motion_lat[c["id"]] = (t - c["_mono"]) * 1000
                    if len(self.motion_lat) > 50:
                        self.motion_lat.pop(next(iter(self.motion_lat)))
            moving = self.camera_moving()
            sel = self.tracker.select(feats, boxes, w, h)
            # 画面倒置（摄像头倒装、没设旋转）：最大的那个人鼻子在髋下方。关键点可见度低、算不出特征时也能看出来
            inverted = False
            if poses:
                big = max(range(len(poses)), key=lambda i: (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
                lm = poses[big].lm
                # 只用看得清的点投票：人离镜头太近时，画面外的髋是模型猜的，不能拿来判断
                if lm[0, 3] >= 0.5 and min(lm[23, 3], lm[24, 3]) >= 0.5:
                    inverted = float(lm[0, 1]) > float(lm[23, 1] + lm[24, 1]) / 2 + 0.05
                    self.inverted_hist.append(inverted)
            if moving:
                reason = "CAMERA_MOVING"
            elif not poses:
                reason = "NO_PERSON"
            elif inverted or (sel.target is not None and feats[sel.target].upright < -0.5):
                reason = "INVERTED"  # 关键点和 f1 都不可信
            elif sel.target is None:
                reason = "LOW_QUALITY" if any(f is None for f in feats) else "NO_PERSON"
            elif sel.ambiguous:
                reason = "AMBIGUOUS"
            else:
                reason = None
            observable = reason is None
            feat = feats[sel.target] if sel.target is not None else None
            body = Body(poses[sel.target].lm, poses[sel.target].world, w, h) if sel.target is not None else None
            ps = self.posture.update(t, feat if observable else None)
            if observable and ps.f1 is not None:
                self.f1_hist.append((t, ps.f1))
                while self.f1_hist and self.f1_hist[0][0] < t - 15:
                    self.f1_hist.popleft()
            if self.calib.active:
                ok = observable and feat is not None
                for e in self.calib.update(t, ok, feat.f1 if ok else None,
                                           feat.hip if ok and feat.hip == feat.hip else None):
                    self._on_calib(e)
            if self.rec.on:
                self.rec.write({"t": round(t, 3), "r": reason, "n": len(poses), "tg": sel.target,
                                "amb": sel.ambiguous, "near": sel.other_near, "mv": moving,
                                "sh": round(self.motion.last_shift, 2),
                                "f1r": _r(feat.f1) if feat else None, "hipr": _r(feat.hip, 1) if feat else None,
                                "kr": _r(feat.knee, 1) if feat else None, "q": _r(feat.quality, 2) if feat else None,
                                "f1": _r(ps.f1), "hip": _r(ps.hip, 1), "cls": ps.raw,
                                "mode": self.session["mode"] if self.session else None,
                                "st": self.session["status"] if self.session else None})
            self._feed(t, observable, reason, ps, sel.other_near, body if observable else None)
            self.view = {
                "observable": observable, "reason": reason,
                "posture": {"raw": ps.raw, "f1": _r(ps.f1), "hip": _r(ps.hip, 0), "knee": _r(ps.knee, 0),
                            "sit_stable": ps.sit_stable, "stand_stable": ps.stand_stable},
                "persons": len(poses), "other_near": sel.other_near,
                "target": sel.target,
                "inverted_hint": len(self.inverted_hist) >= 10 and sum(self.inverted_hist) >= 0.8 * len(self.inverted_hist),
                "skeletons": [[[*(round(float(c), 3) for c in derotate((pt[0], pt[1]), self.s.rotate)),
                                round(float(pt[3]), 2)] for pt in p.lm] for p in poses],
            }
            self.done.append(time.monotonic())
            self.infer_ms = (time.monotonic() - t0) * 1000
            if cap_ms:
                self.latency_ms = time.time() * 1000 - cap_ms

    def _feed(self, t, observable, reason, ps, other_near, body=None) -> None:
        """把一帧（或断流心跳）喂给当前模式的状态机，并处理产生的事件。调用方持有 self.lock。"""
        if not self.session or self.session["status"] != "observing":
            return
        events = []
        if self.calib.active:  # 标定时的坐站是按提示做的动作，不算日常起身
            self._interval(t, observable, reason)
            return
        if self.session["mode"] == "daily":
            events += self.rise.update(t, observable, reason, ps if observable else None, other_near)
        if self.sts.active:
            events += self.sts.update(t, observable, reason, ps, other_near, self._f1_window, body)
        if self.ex and self.ex.active:
            events += self.ex.update(t, Obs(t, observable, reason, ps if observable else None, body, other_near))
        self._interval(t, observable, reason)
        for e in events:
            self._handle(e)

    def _estimator(self):
        """训练会话用 heavy（第一次用时在处理线程里加载），其他时候用默认模型。"""
        s = self.session
        if self.training_est_factory and s and s["mode"] == "training" and s["status"] in ("starting", "observing"):
            if self._training_est is None:
                self._training_est = self.training_est_factory()
            self.model = self.training_model_name
            return self._training_est
        self.model = "full"
        return self.est

    def _f1_window(self, t0: float, t1: float) -> float | None:
        xs = sorted(f for ti, f in self.f1_hist if t0 <= ti <= t1)
        return xs[len(xs) // 2] if xs else None

    def _handle(self, e: dict) -> None:
        sid = self.session["id"]
        if e["kind"] == "rise":
            rid = self.store.add_rise(sid, e, self.wall)
            if e["complete"]:
                d = f"，用时 {e['duration_s']:.1f} 秒" if e["duration_s"] else ""
                text = f"完整看到起身{d}" + ("（旁边有人）" if e["other_person"] else "")
            else:
                text = f"没看全，不计入：{REASON_TEXT.get(e['reason'], e['reason'])}"
            self._push({**e, "id": rid, "text": text})
        elif e["kind"] == "sit_bout":
            self.store.add_sit_bout(sid, e, self.wall)
        elif e["kind"] == "sts_result":
            cond = dict(DEFAULT_CONDITIONS)
            if e.get("arms_reps"):
                cond["armrest"] = "用了手"  # 和「不用扶手」的记录不放在一起比较
            tid = self.store.add_training(sid, "sts30", time.time(), e, cond)
            self.last_sts = {**e, "id": tid, "at": time.time()}
            note = "" if e["valid"] else "（本次无效，不进入趋势）"
            arms = f"，其中 {e['arms_reps']} 次双手没放在胸前" if e.get("arms_reps") else ""
            self._push({**e, "id": tid, "text": f"30 秒坐站：{e['reps']} 次{arms}{note}"})
        elif e["kind"] in ("sts_phase", "sts_rep"):
            self._push(e, store_log=False)
        elif e["kind"] == "ex_result":
            cond = {k: v for k, v in e["params"].items() if k in ("side", "variant", "hold_s", "target_s", "target_reps",
                                                                     "duration_s")}
            cond["preset"] = self.s.presets["front" if e["exercise"] == "weight_shift" else "training"]
            details = self._ex_details(e)
            tid = self.store.add_training(sid, e["exercise"], time.time(), e, cond, details=details)
            self.ex_result = {**e, "id": tid, "at": time.time()}
            self._push({**e, "id": tid, "text": self._ex_text(e)})
            if e["exercise"] == "weight_shift":  # 正面训练位练完，转回侧面训练位
                self.bridge.enqueue("GOTO_PRESET", index=self.s.presets["training"])
        elif e["kind"] in ("ex_rep", "ex_attempt", "ex_note"):
            self._push(e, store_log=bool(e.get("text")))
        elif e["kind"] == "ex_phase":
            self._push(e, store_log=False)

    def _on_calib(self, e: dict) -> None:
        if e["phase"] == "done":
            r = e["result"]
            self.store.save_calibration(r)
            self._load_thresholds()
            self._reset_trackers()
            th = self.posture.th
            text = (f"标定完成：坐 {r['sit_f1']:.2f}、站 {r['stand_f1']:.2f}，"
                    f"阈值 {th.sit_f1:.2f} / {th.stand_f1:.2f}" + ("" if th.stand_hip > 0 else "（髋角不参与）"))
        elif e["phase"] == "failed":
            text = "标定没有完成：" + (e.get("error") or "")
        else:
            text = "标定：" + e["prompt"]
        self._push({**e, "text": text}, store_log=e["phase"] in ("done", "failed"))

    def _push(self, e: dict, store_log: bool = True) -> None:
        e = {**e, "at": time.time()}
        if self.rec.on and e.get("kind") != "session":
            self.rec.write({"t": round(time.monotonic(), 3), "ev": e})
        if store_log:
            self.events.appendleft(e)
        if self.on_event:
            self.on_event(e)

    # ---- 观测区间 ----

    def _interval(self, t: float, observable: bool, reason: str | None) -> None:
        if self.obs is None:
            self.obs = [observable, reason, t]
        elif (self.obs[0], self.obs[1]) != (observable, reason):
            self.store.add_interval(self.session["id"], self.obs[0], self.obs[1], self.wall(self.obs[2]), self.wall(t))
            self.obs = [observable, reason, t]

    def _close_interval(self, t: float) -> None:
        if self.session and self.obs is not None:
            self.store.add_interval(self.session["id"], self.obs[0], self.obs[1], self.wall(self.obs[2]), self.wall(t))
        self.obs = None

    # ---- 心跳（事件循环，每 250 毫秒） ----

    def tick(self) -> None:
        now = time.monotonic()
        with self.lock:
            s = self.session
            if s and s["status"] == "starting":
                cmd = self.bridge.get(s["cmd_id"])
                waited = now - s["_start_mono"]
                if cmd and cmd["state"] in ("done", "failed"):
                    s["preset_ok"] = cmd["state"] == "done"
                elif waited > self.s.move_timeout_s:
                    s["preset_ok"] = False
                if s["preset_ok"] is not None and not self.camera_moving(now):
                    s["status"] = "observing"
                    self.store.set_session(s["id"], status="observing")
                    self._push({"kind": "session", "text": "正在观察" if s["preset_ok"]
                                else "正在观察（没有收到摄像头确认，请看一下摄像头）"})
            if s and s["status"] == "paused" and s.get("preset_ok") is None:
                cmd = self.bridge.get(s["cmd_id"])
                if cmd and cmd["state"] in ("done", "failed"):
                    s["preset_ok"] = cmd["state"] == "done"
            self._check_reminder(now)
            down = self.last_frame_mono is None or now - self.last_frame_mono > self.s.stream_down_s
            if down:
                self.view = {**self.view, "observable": False, "reason": "STREAM_DOWN", "persons": 0,
                             "skeletons": []}
                self.last_t = max(self.last_t, now)
                self._feed(now, False, "STREAM_DOWN", None, False)

    # ---- 给页面的快照 ----

    def snapshot(self) -> dict:
        now = time.monotonic()

        def fps(q):
            xs = [x for x in q if now - x < 3.0]
            return round(len(xs) / 3.0, 1)

        with self.lock:
            sts = self.sts.status(now) if self.sts.phase != "idle" else None
            cur_sit = self.rise.current_sit_s(now) if self.session and self.session["status"] == "observing" else None
            bridge = self.bridge.snapshot()
            bridge["camera_moving"] = self.camera_moving(now)
            for c in bridge["recent"]:
                c["motion_ms"] = _r(self.motion_lat.get(c["id"]), 0)
            lats = list(self.motion_lat.values())[-10:]
            return {
                "server_time": time.time(),
                "session": self.session_public(),
                "view": self.view,
                "stream": {"fps_in": fps(self.recv), "fps_done": fps(self.done),
                           "latency_ms": _r(self.latency_ms, 0), "infer_ms": _r(self.infer_ms, 0),
                           "view_latency_ms": _r(sorted(lats)[len(lats) // 2], 0) if lats else None,
                           "source": self.frame_src, "model": self.model,
                           "frame_age_s": None if self.last_frame_mono is None else round(now - self.last_frame_mono, 1)},
                "bridge": bridge,
                "motion": {"shift": _r(self.motion.last_shift, 2), "response": _r(self.motion.last_response, 2),
                           "moving": self.motion.moving(now)},
                "calib": self.calib.status(now) if self.calib.phase != "idle" else None,
                "thresholds": self.calibration_info(),
                "debug": self.rec.status(),
                "sts": sts,
                "last_sts": self.last_sts,
                "exercise": self.ex.status(now) if self.ex and self.ex.phase != "idle" else None,
                "last_exercise": self.ex_result,
                "plan": self.plan,
                "reminder": None if not self.reminder else {k: v for k, v in self.reminder.items() if k != "seat_start"},
                "demo_fast": self.s.demo_fast,
                "current_sit_s": _r(cur_sit, 0),
                "events": list(self.events)[:12],
                "rotate": self.s.rotate,
            }


def _fmt_min(s: float) -> str:
    m = int(s // 60)
    return f"{int(s)} 秒" if m < 1 else f"{m} 分钟"


def _r(v, nd=3):
    return None if v is None or v != v else round(float(v), nd)

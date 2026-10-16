"""分析引擎：把逐帧处理、会话、观测区间、起身识别、30 秒坐站和事件入库串起来。

线程模型：process() 在线程池里跑（姿态推理较慢），tick() 在事件循环里每 250 毫秒跑一次；
两者都通过 self.lock 修改状态机。
"""

from __future__ import annotations

import threading
import time
from collections import deque

from . import features as F
from .bridge import Bridge
from .config import Settings
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
        self.lock = threading.Lock()
        self.posture, self.rise, self.tracker, self.sts = PostureClassifier(), RiseDetector(), TargetTracker(), StsTest()
        self.session: dict | None = None
        self.obs: list | None = None  # 当前观测区间 [observable, reason, t0]
        self.f1_hist: deque[tuple[float, float]] = deque()
        self.recv: deque[float] = deque(maxlen=90)
        self.done: deque[float] = deque(maxlen=90)
        self.last_frame_mono: float | None = None
        self.latency_ms: float | None = None
        self.infer_ms: float | None = None
        self.view: dict = {"observable": False, "reason": "STREAM_DOWN", "posture": None, "persons": 0,
                           "other_near": False}
        self.events: deque[dict] = deque(maxlen=30)
        self.last_sts: dict | None = None
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
            cmd = self.bridge.enqueue("GOTO_PRESET", index=self.s.presets[mode])
            self.session = {"id": sid, "mode": mode, "label": label, "status": "starting", "cmd_id": cmd["id"],
                            "started_at": time.time(), "preset_ok": None, "_start_mono": time.monotonic()}
            self._reset_trackers()
            self._push({"kind": "session", "text": f"开始{'训练' if mode == 'training' else '观察'}，摄像头正在转向"
                                                    f"{'训练位' if mode == 'training' else '日常区'}"})
            return self.session_public()

    def pause(self) -> dict | None:
        with self.lock:
            if not self.session or self.session["status"] not in ("starting", "observing"):
                return self.session_public()
            self._close_interval(time.monotonic())
            cmd = self.bridge.enqueue("GOTO_PRESET", index=self.s.presets["privacy"])
            self.session.update(status="paused", cmd_id=cmd["id"], preset_ok=None)
            self.store.set_session(self.session["id"], status="paused")
            if self.sts.active:
                self.sts.cancel(time.monotonic())
            self._push({"kind": "session", "text": "已暂停，摄像头正在转向墙面"})
            return self.session_public()

    def resume(self) -> dict | None:
        with self.lock:
            if not self.session or self.session["status"] != "paused":
                return self.session_public()
            cmd = self.bridge.enqueue("GOTO_PRESET", index=self.s.presets[self.session["mode"]])
            self.session.update(status="starting", cmd_id=cmd["id"], preset_ok=None, _start_mono=time.monotonic())
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
            return self.sts.start(time.monotonic())

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
        poses = self.est.detect(rgb, int(t * 1000)) if self.est else []
        feats = [F.compute(p, w, h) for p in poses]
        boxes = [F.bbox_px(p, w, h) for p in poses]
        with self.lock:
            sel = self.tracker.select(feats, boxes, w, h)
            if self.bridge.camera_moving():
                reason = "CAMERA_MOVING"
            elif not poses:
                reason = "NO_PERSON"
            elif sel.target is None:
                reason = "LOW_QUALITY" if any(f is None for f in feats) else "NO_PERSON"
            elif sel.ambiguous:
                reason = "AMBIGUOUS"
            else:
                reason = None
            observable = reason is None
            feat = feats[sel.target] if sel.target is not None else None
            ps = self.posture.update(t, feat if observable else None)
            if observable and ps.f1 is not None:
                self.f1_hist.append((t, ps.f1))
                while self.f1_hist and self.f1_hist[0][0] < t - 15:
                    self.f1_hist.popleft()
            self._feed(t, observable, reason, ps, sel.other_near)
            self.view = {
                "observable": observable, "reason": reason,
                "posture": {"raw": ps.raw, "f1": _r(ps.f1), "hip": _r(ps.hip, 0), "knee": _r(ps.knee, 0),
                            "sit_stable": ps.sit_stable, "stand_stable": ps.stand_stable},
                "persons": len(poses), "other_near": sel.other_near,
                "target": sel.target,
                "skeletons": [[[*(round(float(c), 3) for c in derotate((pt[0], pt[1]), self.s.rotate)),
                                round(float(pt[3]), 2)] for pt in p.lm] for p in poses],
            }
            self.done.append(time.monotonic())
            self.infer_ms = (time.monotonic() - t0) * 1000
            if cap_ms:
                self.latency_ms = time.time() * 1000 - cap_ms

    def _feed(self, t, observable, reason, ps, other_near) -> None:
        """把一帧（或断流心跳）喂给当前模式的状态机，并处理产生的事件。调用方持有 self.lock。"""
        if not self.session or self.session["status"] != "observing":
            return
        events = []
        if self.session["mode"] == "daily":
            events += self.rise.update(t, observable, reason, ps if observable else None, other_near)
        if self.sts.active:
            events += self.sts.update(t, observable, reason, ps, other_near, self._f1_window)
        self._interval(t, observable, reason)
        for e in events:
            self._handle(e)

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
            tid = self.store.add_training(sid, "sts30", time.time(), e, DEFAULT_CONDITIONS)
            self.last_sts = {**e, "id": tid, "at": time.time()}
            note = "" if e["valid"] else "（本次无效，不进入趋势）"
            self._push({**e, "id": tid, "text": f"30 秒坐站：{e['reps']} 次{note}"})
        elif e["kind"] in ("sts_phase", "sts_rep"):
            self._push(e, store_log=False)

    def _push(self, e: dict, store_log: bool = True) -> None:
        e = {**e, "at": time.time()}
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
                if s["preset_ok"] is not None and not self.bridge.camera_moving(now):
                    s["status"] = "observing"
                    self.store.set_session(s["id"], status="observing")
                    self._push({"kind": "session", "text": "正在观察" if s["preset_ok"]
                                else "正在观察（没有收到摄像头确认，请看一下摄像头）"})
            if s and s["status"] == "paused" and s.get("preset_ok") is None:
                cmd = self.bridge.get(s["cmd_id"])
                if cmd and cmd["state"] in ("done", "failed"):
                    s["preset_ok"] = cmd["state"] == "done"
            down = self.last_frame_mono is None or now - self.last_frame_mono > self.s.stream_down_s
            if down:
                self.view = {**self.view, "observable": False, "reason": "STREAM_DOWN", "persons": 0,
                             "skeletons": []}
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
            return {
                "server_time": time.time(),
                "session": self.session_public(),
                "view": self.view,
                "stream": {"fps_in": fps(self.recv), "fps_done": fps(self.done),
                           "latency_ms": _r(self.latency_ms, 0), "infer_ms": _r(self.infer_ms, 0),
                           "frame_age_s": None if self.last_frame_mono is None else round(now - self.last_frame_mono, 1)},
                "bridge": self.bridge.snapshot(),
                "sts": sts,
                "last_sts": self.last_sts,
                "current_sit_s": _r(cur_sit, 0),
                "events": list(self.events)[:12],
                "rotate": self.s.rotate,
            }


def _r(v, nd=3):
    return None if v is None else round(float(v), nd)

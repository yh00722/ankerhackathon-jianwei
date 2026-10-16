"""设备桥的服务端：给安卓 App 下发指令、接收确认 / 状态 / 事件。

App 每 0.5 秒拉一次指令（GET /api/bridge/commands?after=N），执行后回报 ACK。
「镜头转动中」由引擎结合指令状态和画面位移判断（engine.camera_moving），这里只提供最近一条云台指令。
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque

MOVING_TYPES = {"GOTO_PRESET", "PTZ_STEP"}
# 手机断开期间发出的指令不能攒着：几分钟后重连时一口气执行，摄像头会无缘无故乱转
EXPIRE_S = 30.0


class Bridge:
    def __init__(self, store):
        self.store = store
        # 指令编号从启动时刻的毫秒时间戳起算：服务端重启后编号仍然递增，手机端按 after=上次编号 拉取不会漏掉
        self._ids = itertools.count(int(time.time() * 1000))
        self._lock = threading.Lock()
        self.commands: deque[dict] = deque(maxlen=200)
        self.status: dict = {}
        self.status_at: float | None = None  # 最近一次状态上报（monotonic）
        self.poll_at: float | None = None  # 最近一次拉指令（monotonic）
        self.events: deque[dict] = deque(maxlen=50)

    # ---- 下发 ----

    def enqueue(self, type_: str, **args) -> dict:
        cmd = {"id": next(self._ids), "type": type_, "args": args, "state": "pending",
               "issued_at": time.time(), "_mono": time.monotonic(), "ack_mono": None,
               "ok": None, "code": None, "msg": None, "latency_ms": None}
        with self._lock:
            self.commands.append(cmd)
        return public(cmd)

    def pull(self, after: int) -> list[dict]:
        now = time.monotonic()
        self.poll_at = now
        with self._lock:
            out = []
            for c in self.commands:
                if c["state"] == "pending" and now - c["_mono"] > EXPIRE_S:
                    c["state"] = "expired"
                if c["id"] > after and c["state"] in ("pending", "sent"):
                    c["state"] = "sent"
                    out.append({"id": c["id"], "type": c["type"], "args": c["args"]})
            return out

    def ack(self, cid: int, ok: bool, code: int | None, msg: str | None, latency_ms: float | None) -> dict | None:
        with self._lock:
            c = next((c for c in self.commands if c["id"] == cid), None)
            if c is None:
                return None
            c.update(state="done" if ok else "failed", ok=ok, code=code, msg=msg,
                     ack_mono=time.monotonic(),
                     latency_ms=latency_ms if latency_ms is not None else (time.monotonic() - c["_mono"]) * 1000)
        self.store.log_device("ack", {"id": cid, "type": c["type"], "args": c["args"], "ok": ok, "code": code,
                                      "msg": msg}, c["latency_ms"])
        return public(c)

    def get(self, cid: int) -> dict | None:
        c = next((c for c in self.commands if c["id"] == cid), None)
        return public(c) if c else None

    # ---- 上报 ----

    def report_status(self, status: dict) -> None:
        self.status = status
        self.status_at = time.monotonic()

    def report_events(self, events: list[dict]) -> None:
        known = {e.get("id") for e in self.events}
        new = [e for e in events if e.get("id") not in known]
        for e in new:
            self.events.appendleft(e)
        if new:
            self.store.log_device("events", {"events": new})

    # ---- 状态 ----

    def connected(self, now: float | None = None) -> bool:
        now = now or time.monotonic()
        return self.poll_at is not None and now - self.poll_at < 5.0

    def last_move(self) -> dict | None:
        """最近一条云台类指令（含内部时间字段 _mono / ack_mono），没有返回 None。"""
        with self._lock:
            return next((dict(c) for c in reversed(self.commands) if c["type"] in MOVING_TYPES), None)

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "connected": self.connected(now),
            "status": self.status,
            "status_age_s": None if self.status_at is None else round(now - self.status_at, 1),
            "recent": [public(c) for c in list(self.commands)[-6:]],
            "events": list(self.events)[:10],
        }


def public(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_") and k != "ack_mono"}

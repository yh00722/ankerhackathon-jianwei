"""设备桥的服务端：给安卓 App 下发指令、接收确认 / 状态 / 事件。

App 每 0.5 秒拉一次指令（GET /api/bridge/commands?after=N），执行后回报 ACK。
「镜头转动中」= 云台类指令发出后，直到收到确认再加 1.5 秒；一直没确认就按超时算。
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque

MOVING_TYPES = {"GOTO_PRESET", "PTZ_STEP"}


class Bridge:
    def __init__(self, store, move_timeout_s: float = 15.0, settle_s: float = 1.5):
        self.store = store
        self.move_timeout_s, self.settle_s = move_timeout_s, settle_s
        self._ids = itertools.count(1)
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
        self.poll_at = time.monotonic()
        with self._lock:
            out = []
            for c in self.commands:
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

    def camera_moving(self, now: float | None = None) -> bool:
        now = now or time.monotonic()
        with self._lock:
            for c in reversed(self.commands):
                if c["type"] not in MOVING_TYPES:
                    continue
                if c["ack_mono"] is None:
                    if now - c["_mono"] < self.move_timeout_s:
                        return True
                elif now - c["ack_mono"] < self.settle_s:
                    return True
        return False

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "connected": self.connected(now),
            "status": self.status,
            "status_age_s": None if self.status_at is None else round(now - self.status_at, 1),
            "camera_moving": self.camera_moving(now),
            "recent": [public(c) for c in list(self.commands)[-6:]],
            "events": list(self.events)[:10],
        }


def public(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_") and k != "ack_mono"}

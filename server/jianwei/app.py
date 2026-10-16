"""FastAPI 入口：帧输入、实时状态推送、会话、报告、分享授权、设备桥接口。

启动：uvicorn jianwei.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import reports
from .bridge import Bridge
from .config import settings
from .engine import Engine
from .store import Store


class SessionIn(BaseModel):
    mode: str = "daily"  # daily / training
    label: str = "alone"  # alone / companion


class ShareIn(BaseModel):
    level: str  # none / summary


class AssistIn(BaseModel):
    assist: str  # yes / no / unsure


class CommandIn(BaseModel):
    type: str
    args: dict = {}


class AckIn(BaseModel):
    id: int
    ok: bool
    code: int | None = None
    msg: str | None = None
    latency_ms: float | None = None


class ConfigIn(BaseModel):
    rotate: int | None = None


def create_app(estimator_factory=None, db_path: str | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(db_path or settings.db_path)
        store.purge_older_than(settings.retention_days)
        store.seed_samples()
        bridge = Bridge(store, settings.move_timeout_s, settings.move_settle_s)
        est = estimator_factory() if estimator_factory else _default_estimator()
        engine = Engine(settings, store, bridge, est)
        loop = asyncio.get_running_loop()
        clients: set[asyncio.Queue] = set()
        engine.on_event = lambda e: loop.call_soon_threadsafe(_fanout, clients, {"type": "event", "event": e})
        state.update(store=store, bridge=bridge, engine=engine, clients=clients, frame=None,
                     new_frame=asyncio.Event(), pool=ThreadPoolExecutor(max_workers=1))
        tasks = [asyncio.create_task(_process_loop(state)), asyncio.create_task(_tick_loop(state))]
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="见微知行 分析服务", lifespan=lifespan)
    app.state.jw = state

    # ---- 健康与快照 ----

    @app.get("/api/health")
    def health():
        return {"ok": True, "time": time.time()}

    @app.get("/api/state")
    def get_state():
        return state["engine"].snapshot()

    @app.post("/api/config")
    def set_config(body: ConfigIn):
        if body.rotate is not None:
            if body.rotate not in (0, 90, 180, 270):
                raise HTTPException(400, "rotate 只能是 0 / 90 / 180 / 270")
            settings.rotate = body.rotate
        return {"rotate": settings.rotate}

    # ---- 会话 ----

    @app.post("/api/sessions")
    def start_session(body: SessionIn):
        if body.mode not in ("daily", "training") or body.label not in ("alone", "companion"):
            raise HTTPException(400, "mode / label 取值不对")
        return state["engine"].start_session(body.mode, body.label)

    @app.post("/api/sessions/current/{action}")
    def session_action(action: str):
        eng = state["engine"]
        fn = {"pause": eng.pause, "resume": eng.resume, "stop": eng.stop}.get(action)
        if not fn:
            raise HTTPException(404)
        return fn() or {}

    @app.post("/api/training/sts/{action}")
    def sts_action(action: str):
        eng = state["engine"]
        if action == "start":
            r = eng.start_sts()
            if "error" in r:
                raise HTTPException(409, r["error"])
            return r
        if action == "cancel":
            return eng.cancel_sts()
        raise HTTPException(404)

    @app.post("/api/rise_events/{rid}/confirm")
    def confirm(rid: int, body: AssistIn):
        if body.assist not in ("yes", "no", "unsure"):
            raise HTTPException(400)
        state["store"].exec("UPDATE rise_events SET assist = ? WHERE id = ?", (body.assist, rid))
        return {"id": rid, "assist": body.assist}

    # ---- 报告与分享 ----

    @app.get("/api/elder/today")
    def elder_today():
        return reports.elder_today(state["store"])

    @app.get("/api/share")
    def get_share():
        s = state["store"].share()
        return {"level": s["level"], "family_token": s["family_token"], "updated_at": s["updated_at"],
                "revoked_at": s["revoked_at"]}

    @app.put("/api/share")
    def put_share(body: ShareIn):
        if body.level not in ("none", "summary"):
            raise HTTPException(400)
        s = state["store"].set_share(body.level)
        return {"level": s["level"], "updated_at": s["updated_at"], "revoked_at": s["revoked_at"]}

    @app.get("/api/family/summary")
    def family(token: str = Query(...)):
        store = state["store"]
        s = store.share()
        if token != s["family_token"]:
            store.log_access("unknown", "/api/family/summary", False)
            raise HTTPException(403, detail={"locked": True, "reason": "not_paired"})
        if s["level"] == "none":
            store.log_access("family", "/api/family/summary", False)
            raise HTTPException(403, detail={"locked": True, "reason": "revoked" if s["revoked_at"] else "not_shared",
                                             "revoked_at": s["revoked_at"]})
        store.log_access("family", "/api/family/summary", True)
        return {"level": s["level"], **reports.family_summary(store)}

    @app.get("/api/access_log")
    def access_log():
        return state["store"].all("SELECT who, path, at, allowed FROM access_log ORDER BY id DESC LIMIT 20")

    @app.delete("/api/data")
    def delete_data(scope: str = Query(...), confirm: bool = Query(False)):
        if not confirm:
            raise HTTPException(400, "删除需要二次确认（confirm=true）")
        if scope not in ("today", "all"):
            raise HTTPException(400)
        since = reports.day_start(time.time()) if scope == "today" else None
        state["store"].delete(since)
        return {"deleted": scope}

    # ---- 设备 ----

    @app.post("/api/device/command")
    def device_command(body: CommandIn):
        return state["bridge"].enqueue(body.type, **body.args)

    @app.get("/api/bridge/config")
    def bridge_config():
        return {"camera_sn": settings.camera_sn, "presets": settings.presets}

    @app.get("/api/bridge/commands")
    def bridge_pull(after: int = 0):
        return state["bridge"].pull(after)

    @app.post("/api/bridge/ack")
    def bridge_ack(body: AckIn):
        r = state["bridge"].ack(body.id, body.ok, body.code, body.msg, body.latency_ms)
        if r is None:
            raise HTTPException(404)
        return r

    @app.post("/api/bridge/status")
    async def bridge_status(req: Request):
        state["bridge"].report_status(await req.json())
        return {"ok": True}

    @app.post("/api/bridge/events")
    async def bridge_events(req: Request):
        body = await req.json()
        state["bridge"].report_events(body if isinstance(body, list) else body.get("events", []))
        return {"ok": True}

    # ---- WebSocket ----

    @app.websocket("/ws/frames")
    async def ws_frames(ws: WebSocket):
        """二进制帧：4 字节大端头长度 + 头 JSON（ts 微秒、cap 采集时刻毫秒）+ JPEG。"""
        await ws.accept()
        eng = state["engine"]
        try:
            while True:
                data = await ws.receive_bytes()
                (hl,) = struct.unpack(">I", data[:4])
                head = json.loads(data[4:4 + hl])
                now = time.monotonic()
                eng.recv.append(now)
                eng.last_frame_mono = now
                state["frame"] = (data[4 + hl:], now, head.get("cap"))
                state["new_frame"].set()
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        state["clients"].add(q)
        try:
            while True:
                msg = await q.get()
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            state["clients"].discard(q)

    return app


def _fanout(clients: set, msg: dict) -> None:
    for q in list(clients):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(msg)


async def _process_loop(state: dict) -> None:
    """只处理最新一帧：处理不过来就跳帧，保证实时。"""
    loop = asyncio.get_running_loop()
    while True:
        await state["new_frame"].wait()
        state["new_frame"].clear()
        frame = state["frame"]
        if frame is None:
            continue
        try:
            await loop.run_in_executor(state["pool"], state["engine"].process, *frame)
        except Exception as e:  # 单帧失败不影响后续
            print("process error:", repr(e))


async def _tick_loop(state: dict) -> None:
    while True:
        await asyncio.sleep(0.125)
        eng = state["engine"]
        eng.tick()
        _fanout(state["clients"], {"type": "state", "state": eng.snapshot()})


def _default_estimator():
    from .pose import PoseEstimator

    return PoseEstimator(settings.model_path)


app = create_app()

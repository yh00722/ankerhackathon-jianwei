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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import reports
from .bridge import Bridge
from .config import load_runtime, save_runtime, settings
from .engine import Engine
from .store import Store


class SessionIn(BaseModel):
    mode: str = "daily"  # daily / training
    label: str = "alone"  # alone / companion


class ShareIn(BaseModel):
    level: str | None = None  # 家属：none / summary
    therapist_level: str | None = None  # 康复师：none / full


class PlanIn(BaseModel):
    enabled: bool | None = None
    params: dict | None = None


class SupportIn(BaseModel):
    support: str  # yes / no


class ReminderIn(BaseModel):
    response: str  # ok / later


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
    demo_fast: bool | None = None


class RecordIn(BaseModel):
    on: bool


class BenchIn(BaseModel):
    n: int = 40
    models: list[str] = ["full", "heavy"]


def create_app(estimator_factory=None, db_path: str | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db_path is None:
            load_runtime(settings, settings.db_path)  # 测试用临时库时不读
        store = Store(db_path or settings.db_path)
        store.purge_older_than(settings.retention_days)
        store.close_stale_sessions()
        store.seed_samples()
        bridge = Bridge(store)
        est = estimator_factory() if estimator_factory else _default_estimator()
        engine = Engine(settings, store, bridge, est)
        if not estimator_factory and settings.training_model_path and Path(settings.training_model_path).exists():
            engine.training_est_factory = lambda: _default_estimator(settings.training_model_path)
            engine.training_model_name = Path(settings.training_model_path).stem.split("_")[-1]
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
        if body.demo_fast is not None:
            settings.demo_fast = body.demo_fast
        if body.rotate is not None or body.demo_fast is not None:
            save_runtime(settings, db_path or settings.db_path)
        return {"rotate": settings.rotate, "demo_fast": settings.demo_fast}

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

    # ---- 标定（按预置位） ----

    @app.get("/api/calibration")
    def get_calibration():
        store, eng = state["store"], state["engine"]
        return {"current": eng.calibration_info(),
                "presets": {name: store.calibration(idx) for name, idx in settings.presets.items() if name != "privacy"}}

    @app.post("/api/calibration/{action}")
    def calibration_action(action: str):
        eng = state["engine"]
        if action == "start":
            r = eng.start_calibration()
            if "error" in r:
                raise HTTPException(409, r["error"])
            return r
        if action == "cancel":
            return eng.cancel_calibration()
        raise HTTPException(404)

    @app.delete("/api/calibration/{preset}")
    def delete_calibration(preset: int):
        state["engine"].reset_calibration(preset)
        return {"deleted": preset}

    # ---- 调试（团队自测用，演示前删除记录） ----

    @app.post("/api/debug/record")
    def debug_record(body: RecordIn):
        eng = state["engine"]
        with eng.lock:
            if body.on:
                eng.rec.start()
            else:
                eng.rec.stop()
            return eng.rec.status()

    @app.get("/api/debug/records")
    def debug_records():
        return state["engine"].rec.files()

    @app.delete("/api/debug/records")
    def debug_records_delete():
        eng = state["engine"]
        with eng.lock:
            return {"deleted": eng.rec.delete_all()}

    @app.post("/api/debug/bench")
    async def debug_bench(body: BenchIn):
        """在内存里收 n 帧，分别用不同模型推理，比较速度和结果；帧用完即丢，不落盘。"""
        from . import bench

        if state.get("bench"):
            raise HTTPException(409, "已有一个测速在进行")
        bad = [m for m in body.models if m not in bench.MODELS]
        if bad:
            raise HTTPException(400, f"不认识的模型：{bad}")
        state["bench"] = {"need": max(5, min(body.n, 150)), "frames": [], "done": asyncio.Event()}
        try:
            await asyncio.wait_for(state["bench"]["done"].wait(), timeout=30)
            frames = state["bench"]["frames"]
            return await asyncio.to_thread(bench.run, frames, body.models, settings.rotate, settings.infer_width)
        except asyncio.TimeoutError:
            raise HTTPException(504, "30 秒内没收够帧：控制台出流了吗？")
        finally:
            state["bench"] = None

    # ---- 训练计划与训练动作 ----

    @app.get("/api/plan")
    def get_plan():
        return state["store"].plan()

    @app.put("/api/plan/{kind}")
    def put_plan(kind: str, body: PlanIn):
        from .store import PLAN_DEFAULTS

        if kind not in PLAN_DEFAULTS:
            raise HTTPException(404, f"不认识的动作：{kind}")
        params = _clean_params(kind, body.params or {})
        item = state["store"].set_plan(kind, body.enabled, params)
        state["engine"].reload_plan()
        return item

    @app.post("/api/training/exercise/{kind}/start")
    def exercise_start(kind: str):
        r = state["engine"].start_exercise(kind)
        if "error" in r:
            raise HTTPException(409, r["error"])
        return r

    @app.post("/api/training/exercise/cancel")
    def exercise_cancel():
        return state["engine"].cancel_exercise()

    @app.post("/api/training/exercise/self_report")
    def exercise_self_report(body: SupportIn):
        if body.support not in ("yes", "no"):
            raise HTTPException(400)
        r = state["engine"].exercise_self_report(body.support)
        if r is None:
            raise HTTPException(409, "现在没有可以补充的记录")
        return r

    @app.post("/api/reminders/{rid}/respond")
    def reminder_respond(rid: int, body: ReminderIn):
        if body.response not in ("ok", "later"):
            raise HTTPException(400)
        r = state["engine"].respond_reminder(rid, body.response)
        if r is None:
            raise HTTPException(409, "这条提醒已经结束")
        return r

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
                "revoked_at": s["revoked_at"], "therapist_level": s["therapist_level"],
                "therapist_token": s["therapist_token"], "therapist_revoked_at": s["therapist_revoked_at"]}

    @app.put("/api/share")
    def put_share(body: ShareIn):
        store = state["store"]
        if body.level is None and body.therapist_level is None:
            raise HTTPException(400)
        if body.level is not None:
            if body.level not in ("none", "summary"):
                raise HTTPException(400)
            store.set_share(body.level)
        if body.therapist_level is not None:
            if body.therapist_level not in ("none", "full"):
                raise HTTPException(400)
            store.set_therapist_share(body.therapist_level)
        s = store.share()
        return {"level": s["level"], "updated_at": s["updated_at"], "revoked_at": s["revoked_at"],
                "therapist_level": s["therapist_level"], "therapist_revoked_at": s["therapist_revoked_at"]}

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
        summary = reports.family_summary(store)
        summary["rewrite"] = reports.llm_rewrite([f"本周按计划练了 {summary['trained_days']} 天。"] + summary["lines"],
                                                 reports.llm_caller())
        return {"level": s["level"], **summary}

    @app.get("/api/therapist/summary")
    def therapist(token: str = Query(...)):
        store = state["store"]
        s = store.share()
        if token != s["therapist_token"]:
            store.log_access("unknown", "/api/therapist/summary", False)
            raise HTTPException(403, detail={"locked": True, "reason": "not_paired"})
        if s["therapist_level"] != "full":
            store.log_access("therapist", "/api/therapist/summary", False)
            raise HTTPException(403, detail={"locked": True,
                                             "reason": "revoked" if s["therapist_revoked_at"] else "not_shared",
                                             "revoked_at": s["therapist_revoked_at"]})
        store.log_access("therapist", "/api/therapist/summary", True)
        return reports.therapist_summary(store)

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
        if r["type"] == "SAVE_PRESET" and r["ok"] and "index" in r["args"]:
            state["engine"].reset_calibration(int(r["args"]["index"]))  # 预置位重存后，旧标定作废
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
        """二进制帧：4 字节大端头长度 + 头 JSON（cap 拍摄时刻毫秒、src 来源）+ JPEG。

        帧的时刻用拍摄时刻（浏览器直取帧按 RTP 时间戳还原，帧成批到达也不影响用时计算），
        换算到本机 monotonic 时钟；没有或不可信（超过 3 秒）时用收到的时刻。"""
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
                cap = head.get("cap")
                t = now
                if isinstance(cap, (int, float)):
                    age = time.time() - cap / 1000
                    if 0 <= age < 3.0:
                        t = now - age
                eng.frame_src = head.get("src")
                state["frame"] = (data[4 + hl:], t, cap)
                state["new_frame"].set()
                b = state.get("bench")
                if b and len(b["frames"]) < b["need"]:
                    b["frames"].append((data[4 + hl:], now))
                    if len(b["frames"]) >= b["need"]:
                        b["done"].set()
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


def _clean_params(kind: str, params: dict) -> dict:
    """训练计划参数按默认值的类型转换；不认识的键丢掉，取值不对报 400。"""
    from .exercises import EXERCISES, VARIANT_TEXT
    from .store import PLAN_DEFAULTS

    defaults = {**PLAN_DEFAULTS[kind][1], **(EXERCISES[kind].DEFAULTS if kind in EXERCISES else {})}
    out = {}
    for k, v in params.items():
        if k not in defaults:
            continue
        d = defaults[k]
        try:
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, int):
                v = int(v)
            elif isinstance(d, float):
                v = float(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} 取值不对")
        if k == "side" and v not in ("both", "left", "right"):
            raise HTTPException(400, "side 只能是 both / left / right")
        if k == "variant" and v not in VARIANT_TEXT:
            raise HTTPException(400, "variant 取值不对")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not (0 < v <= 600):
            raise HTTPException(400, f"{k} 超出范围")
        out[k] = v
    return out


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


def _default_estimator(path: str | None = None):
    from .pose import PoseEstimator

    return PoseEstimator(path or settings.model_path)


app = create_app()

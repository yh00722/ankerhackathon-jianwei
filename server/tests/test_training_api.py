"""训练计划、训练动作（走完整的引擎流程）、久坐提醒、康复师授权、大模型措辞核对。"""

import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from jianwei import reports
from jianwei.app import create_app
from jianwei.bridge import Bridge
from jianwei.config import Settings, settings
from jianwei.engine import Engine
from jianwei.features import PoseObs
from jianwei.store import Store
from skeleton import side


class NoPose:
    def detect(self, rgb, ts):
        return []


@pytest.fixture()
def client(tmp_path):
    settings.move_wait_s = 0.0
    app = create_app(estimator_factory=NoPose, db_path=str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c


def test_plan_lists_all_exercises_and_validates_params(client):
    plan = client.get("/api/plan").json()
    kinds = [p["kind"] for p in plan]
    assert kinds == ["sts30", "knee_ext", "knee_flex", "balance", "march2min", "weight_shift"]
    assert [p["kind"] for p in plan if p["enabled"]] == ["sts30", "knee_ext"]
    r = client.put("/api/plan/knee_flex", json={"enabled": True, "params": {"target_reps": "8", "side": "left"}})
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["params"]["target_reps"] == 8
    assert client.put("/api/plan/knee_flex", json={"params": {"side": "middle"}}).status_code == 400
    assert client.put("/api/plan/nope", json={"enabled": True}).status_code == 404
    assert client.get("/api/state").json()["plan"][2]["enabled"] is True  # 引擎里的计划同步更新


def test_exercise_needs_training_session_and_plan(client):
    assert client.post("/api/training/exercise/knee_ext/start").status_code == 409
    s = client.post("/api/sessions", json={"mode": "training", "label": "alone"}).json()
    cmd = client.get("/api/bridge/commands", params={"after": 0}).json()[-1]
    client.post("/api/bridge/ack", json={"id": cmd["id"], "ok": True, "code": 0})
    for _ in range(40):
        if client.get("/api/state").json()["session"]["status"] == "observing":
            break
        time.sleep(0.05)
    assert s["mode"] == "training"
    assert client.post("/api/training/exercise/balance/start").status_code == 409  # 计划里没打开
    r = client.post("/api/training/exercise/knee_ext/start")
    assert r.status_code == 200 and r.json()["phase"] == "prep"
    assert client.get("/api/state").json()["exercise"]["exercise"] == "knee_ext"
    assert client.post("/api/training/sts/start").status_code == 409  # 互斥
    client.post("/api/training/exercise/cancel")
    assert client.get("/api/state").json()["exercise"]["phase"] == "aborted"


def test_therapist_summary_requires_elder_consent(client):
    share = client.get("/api/share").json()
    token = share["therapist_token"]
    assert share["therapist_level"] == "none" and token
    r = client.get("/api/therapist/summary", params={"token": token})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "not_shared"
    client.put("/api/share", json={"therapist_level": "full"})
    r = client.get("/api/therapist/summary", params={"token": token})
    assert r.status_code == 200 and "daily" in r.json() and "sts30" in r.json()["exercises"]
    assert client.get("/api/therapist/summary", params={"token": "wrong"}).status_code == 403
    client.put("/api/share", json={"therapist_level": "none"})
    r = client.get("/api/therapist/summary", params={"token": token})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "revoked"
    # 家属的分享状态不受影响
    assert client.get("/api/share").json()["level"] == "none"


# ---------------- 走完整引擎：合成人体 → 坐位伸膝 → 入库 ----------------


class ScriptedPose:
    """按脚本返回合成人体（侧面坐着，右膝角度随时间变化）。"""

    def __init__(self):
        self.knee = 90.0

    def detect(self, rgb, ts):
        b = side(sitting=True, knee_l=90.0, knee_r=self.knee)
        return [PoseObs(b.lm, b.world)]


def make_engine(tmp_path, est=None):
    s = Settings()
    s.db_path = str(tmp_path / "e.db")
    s.move_wait_s = 0.0
    store = Store(s.db_path)
    return Engine(s, store, Bridge(store), est or NoPose()), store


def observing(eng, mode):
    eng.start_session(mode, "alone")
    cmd = eng.bridge.last_move()
    eng.bridge.ack(cmd["id"], True, 0, None, 50)
    eng.last_frame_mono = time.monotonic()
    eng.tick()
    assert eng.session["status"] == "observing"


def test_knee_extension_end_to_end_is_stored_with_details(tmp_path):
    est = ScriptedPose()
    eng, store = make_engine(tmp_path, est)
    store.set_plan("knee_ext", True, {"target_reps": 3, "hold_s": 2.0, "side": "right"})
    eng.reload_plan()
    observing(eng, "training")
    assert "error" not in eng.start_exercise("knee_ext")
    jpeg = cv2.imencode(".jpg", np.zeros((540, 960, 3), np.uint8))[1].tobytes()
    t = time.monotonic()

    def run(seconds, knee_of):
        nonlocal t
        n = int(seconds * 15)
        for i in range(n):
            est.knee = knee_of(i / max(1, n - 1))
            eng.process(jpeg, t, None)
            t += 1 / 15

    run(1.5, lambda k: 90)
    for _ in range(3):
        run(0.8, lambda k: 90 + 75 * k)
        run(2.4, lambda k: 165)
        run(0.8, lambda k: 165 - 75 * k)
        run(1.0, lambda k: 90)
    rows = reports.training_rows(store, kind="knee_ext")
    assert len(rows) == 1 and rows[0]["reps"] == 3 and rows[0]["valid"] == 1
    d = rows[0]["details"]
    assert d["by_side"] == {"right": 3} and d["reps_hold_ok"] == 3
    today = reports.elder_today(store)
    assert today["exercises"][0]["line"].startswith("坐位伸膝：3 次")


# ---------------- 久坐提醒 ----------------


def test_sedentary_reminder_fires_snoozes_and_ends_when_standing(tmp_path):
    eng, store = make_engine(tmp_path)
    eng.s.demo_fast, eng.s.sedentary_demo_s, eng.s.snooze_demo_s = True, 60, 0.2
    pushed = []
    eng.on_event = pushed.append
    observing(eng, "daily")
    now = time.monotonic()
    eng.rise.state, eng.rise.seat_start = "seated", now - 61  # 已经连续坐了 61 秒
    eng.last_frame_mono = now
    eng.tick()
    rem = [e for e in pushed if e["kind"] == "reminder"]
    assert len(rem) == 1 and "连续坐了 1 分钟" in rem[0]["text"]
    assert eng.respond_reminder(rem[0]["id"], "later")["response"] == "later"
    time.sleep(0.25)
    eng.last_frame_mono = time.monotonic()
    eng.tick()
    assert [e.get("again") for e in pushed if e["kind"] == "reminder"] == [None, True]  # 等一会儿后再提醒一次
    eng.rise.state = "standing"
    eng.tick()
    ends = [e for e in pushed if e["kind"] == "reminder_end"]
    assert ends and ends[0]["by"] == "stood_up" and eng.reminder is None
    row = store.one("SELECT * FROM reminders")
    assert row["response"] == "later" and row["ended_by"] == "stood_up"
    assert reports.elder_today(store)["reminders"] == {"count": 1, "stood_up": 1}
    # 同一段坐不重复提醒
    eng.rise.state = "seated"
    eng.tick()
    assert len([e for e in pushed if e["kind"] == "reminder"]) == 2


# ---------------- 大模型措辞：数字核对 ----------------


def test_llm_rewrite_falls_back_unless_numbers_match():
    lines = ["本周练了 3 天。", "我一个人的时段：开了 2 小时，看到完整站起来 12 次。"]
    reports._LLM_CACHE.clear()
    assert reports.llm_rewrite(lines, None) is None  # 没配置：用模板
    assert reports.llm_rewrite(lines, lambda p: "本周练了 4 天，站起来 12 次。", wait=True) is None  # 数字对不上
    reports._LLM_CACHE.clear()
    assert reports.llm_rewrite(lines, lambda p: "本周练了 3 天，恢复得不错。", wait=True) is None  # 评价性词语
    reports._LLM_CACHE.clear()
    good = reports.llm_rewrite(lines, lambda p: "这周练了 3 天；独处的 2 小时里，完整看到站起来 12 次。", wait=True)
    assert good and good["source"] == "llm"

    def boom(p):
        raise TimeoutError

    reports._LLM_CACHE.clear()
    assert reports.llm_rewrite(lines, boom, wait=True) is None


# ---------------- 趋势与 AI 总结 ----------------


def test_demo_history_trends_and_summaries(client):
    assert client.post("/api/demo/seed").json()["days"] == 13
    assert client.post("/api/demo/seed").json()["already"] is True  # 不重复生成
    tr = client.get("/api/trends").json()
    assert len(tr["daily"]) == 14 and tr["has_sample"] is True
    assert sum(r["rises_complete"] for r in tr["daily"]) > 50
    assert tr["ai"]["source"] == "template" and "含示例数据" in tr["ai"]["text"]
    assert client.get("/api/trends", params={"audience": "family"}).status_code == 403
    # 家属：授权后才有趋势，而且只有次数和训练天数
    share = client.get("/api/share").json()
    client.put("/api/share", json={"level": "summary"})
    fam = client.get("/api/family/summary", params={"token": share["family_token"]}).json()
    assert set(fam["trends"]["daily"][0]) == {"day", "rises_complete", "trainings", "observed", "sample"}
    assert "用时" not in fam["ai"]["text"] and "保持" not in fam["ai"]["text"]
    # 康复师：全部指标
    client.put("/api/share", json={"therapist_level": "full"})
    th = client.get("/api/therapist/summary", params={"token": share["therapist_token"]}).json()
    assert "knee_ext" in th["trends"]["exercises"] and any("用时" in f for f in th["ai"]["facts"])
    client.delete("/api/demo/seed")
    assert client.get("/api/trends").json()["has_sample"] is True  # 启动时自带的两条示例坐站还在
    assert sum(r["rises_complete"] for r in client.get("/api/trends").json()["daily"]) == 0


def test_ai_summary_checks_numbers_and_wording():
    facts = [{"audience": "all", "text": "近 7 天平均每天完整看到站起来 9 次；再之前 7 天平均 6 次。"},
             {"audience": "therapist", "text": "离座到站直的用时：近 7 天平均 2.3 秒，再之前 7 天 3.0 秒。"}]
    reports._LLM_CACHE.clear()
    fam = reports.ai_summary(facts, "family", lambda p: "这周平均每天站起来 9 次，之前是 6 次。", wait=True)
    assert fam["source"] == "llm" and "用时" not in "".join(fam["facts"])
    reports._LLM_CACHE.clear()
    bad = reports.ai_summary(facts, "family", lambda p: "这周平均每天站起来 9 次，恢复得很好。", wait=True)
    assert bad["source"] == "template"
    reports._LLM_CACHE.clear()
    th = reports.ai_summary(facts, "therapist", lambda p: "起身用时从 3.0 秒到 2.3 秒；每天完整站起来平均 9 次（之前 6 次）。", wait=True)
    assert th["source"] == "llm"


def test_llm_rewrite_runs_in_background_then_serves_from_cache():
    import threading

    lines = ["最近 7 天里有 4 天做了训练。"]
    reports._LLM_CACHE.clear()
    gate = threading.Event()

    def slow(p):
        gate.wait(2)
        return "这 7 天里练了 4 天。"

    assert reports.llm_rewrite(lines, slow) is None  # 先用模板，不等模型
    assert reports.llm_rewrite(lines, slow) is None  # 同一份数据不会重复调用
    gate.set()
    for _ in range(50):
        r = reports.llm_rewrite(lines, slow)
        if r:
            break
        time.sleep(0.02)
    assert r and r["source"] == "llm" and r["text"] == "这 7 天里练了 4 天。"

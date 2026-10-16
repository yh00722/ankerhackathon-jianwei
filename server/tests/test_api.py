"""接口测试：用假的姿态模型和临时数据库跑完整的会话、设备桥、分享授权流程。"""

import time

import pytest
from fastapi.testclient import TestClient

from jianwei.app import create_app
from jianwei.config import settings


class NoPose:
    def detect(self, rgb, ts):
        return []


@pytest.fixture()
def client(tmp_path):
    settings.move_wait_s = 0.0
    app = create_app(estimator_factory=NoPose, db_path=str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c


def wait_for(fn, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return None


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_session_turns_camera_and_waits_for_ack(client):
    s = client.post("/api/sessions", json={"mode": "daily", "label": "alone"}).json()
    assert s["status"] == "starting"
    cmds = client.get("/api/bridge/commands", params={"after": 0}).json()
    assert cmds[-1]["type"] == "GOTO_PRESET" and cmds[-1]["args"]["index"] == 1
    # 设备确认前不能显示「正在观察」
    time.sleep(0.3)
    assert client.get("/api/state").json()["session"]["status"] == "starting"
    client.post("/api/bridge/ack", json={"id": cmds[-1]["id"], "ok": True, "code": 0, "latency_ms": 3100})
    st = wait_for(lambda: client.get("/api/state").json()["session"]["status"] == "observing")
    assert st
    # 暂停 → 隐私位
    client.post("/api/sessions/current/pause")
    cmds = client.get("/api/bridge/commands", params={"after": cmds[-1]["id"]}).json()
    assert cmds[-1]["args"]["index"] == 3
    assert client.get("/api/state").json()["session"]["status"] == "paused"


def test_bridge_connected_flag_follows_polling(client):
    assert client.get("/api/state").json()["bridge"]["connected"] is False
    client.get("/api/bridge/commands", params={"after": 0})
    assert client.get("/api/state").json()["bridge"]["connected"] is True


def test_share_flow_and_access_log(client):
    token = client.get("/api/share").json()["family_token"]
    r = client.get("/api/family/summary", params={"token": token})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "not_shared"
    assert client.get("/api/family/summary", params={"token": "wrong"}).status_code == 403
    client.put("/api/share", json={"level": "summary"})
    r = client.get("/api/family/summary", params={"token": token})
    assert r.status_code == 200 and "disclaimer" in r.json()
    # 示例历史在场：30 秒坐站给出同条件对照
    assert r.json()["sts"]["last"]["is_sample"] is True
    client.put("/api/share", json={"level": "none"})
    r = client.get("/api/family/summary", params={"token": token})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "revoked"
    log = client.get("/api/access_log").json()
    assert [x["allowed"] for x in log[:3]] == [0, 1, 0]


def test_delete_requires_confirmation(client):
    assert client.delete("/api/data", params={"scope": "all"}).status_code == 400
    assert client.delete("/api/data", params={"scope": "all", "confirm": True}).status_code == 200


def test_sts_requires_training_session(client):
    assert client.post("/api/training/sts/start").status_code == 409


def test_calibration_needs_observing_session(client):
    assert client.post("/api/calibration/start").status_code == 409
    cur = client.get("/api/calibration").json()["current"]
    assert cur["calibrated"] is False and cur["sit_f1"] == 0.45


def test_saving_a_preset_drops_its_calibration(client):
    store = client.app.state.jw["store"]
    store.save_calibration({"preset": 1, "sit_f1": 0.2, "stand_f1": 0.9, "sit_hip": 100.0, "stand_hip": 170.0})
    assert client.get("/api/calibration").json()["presets"]["daily"]["sit_f1"] == 0.2
    c = client.post("/api/device/command", json={"type": "SAVE_PRESET", "args": {"index": 1}}).json()
    client.post("/api/bridge/ack", json={"id": c["id"], "ok": True, "code": 0})
    assert client.get("/api/calibration").json()["presets"]["daily"] is None


def test_debug_record_writes_numbers_and_can_be_deleted(client, tmp_path):
    eng = client.app.state.jw["engine"]
    eng.rec.dir = tmp_path / "debug"
    assert client.post("/api/debug/record", json={"on": True}).json()["recording"] is True
    eng.rec.write({"t": 1.0, "f1": 0.2})
    assert client.post("/api/debug/record", json={"on": False}).json()["recording"] is False
    assert len(client.get("/api/debug/records").json()) == 1
    assert client.request("DELETE", "/api/debug/records").json()["deleted"] == 1
    assert client.get("/api/debug/records").json() == []


def test_stale_commands_expire_instead_of_running_on_reconnect(client):
    bridge = client.app.state.jw["bridge"]
    c = bridge.enqueue("PTZ_STEP", dir="LEFT")
    bridge.commands[-1]["_mono"] -= 31  # 手机断开时发出、31 秒后才重连
    assert client.get("/api/bridge/commands", params={"after": 0}).json() == []
    assert bridge.get(c["id"])["state"] == "expired"


def test_inverted_picture_is_not_observable():
    """摄像头倒装又没设旋转：人在画面里是倒的（肩在髋下方），不能当成坐着记。"""
    import numpy as np

    from jianwei import features as F

    lm = np.zeros((33, 4), dtype=np.float32)
    lm[:, 3] = 0.9
    for i, (x, y) in {0: (0.5, 0.85), 11: (0.45, 0.7), 12: (0.55, 0.7), 23: (0.46, 0.4), 24: (0.54, 0.4),
                      25: (0.46, 0.25), 26: (0.54, 0.25), 27: (0.46, 0.1), 28: (0.54, 0.1)}.items():
        lm[i, :2] = (x, y)
    world = np.zeros((33, 3), dtype=np.float32)
    world[:, :2] = lm[:, :2]
    f = F.compute(F.PoseObs(lm, world), 960, 540)
    assert f is not None and f.upright < -0.5 and f.f1 < 0

    import cv2

    from jianwei.bridge import Bridge
    from jianwei.config import Settings
    from jianwei.engine import Engine

    class Store:
        def log_device(self, *a, **k):
            pass

        def calibration(self, preset):
            return None

    class Est:
        def detect(self, rgb, ts):
            return [F.PoseObs(lm, world)]

    eng = Engine(Settings(), Store(), Bridge(Store()), Est())
    jpeg = cv2.imencode(".jpg", np.zeros((540, 960, 3), np.uint8))[1].tobytes()
    for i in range(12):
        eng.process(jpeg, 100.0 + i / 14, None)
    assert eng.view["reason"] == "INVERTED" and eng.view["inverted_hint"] is True


def test_rotation_is_saved_next_to_the_database(client, tmp_path):
    assert client.post("/api/config", json={"rotate": 180}).json()["rotate"] == 180
    assert '"rotate": 180' in (tmp_path / "runtime.json").read_text()
    client.post("/api/config", json={"rotate": 0})


def test_pause_and_stop_turn_tracking_off_before_facing_the_wall(client):
    """AI 追踪开着时，转向墙面后会被追踪带回来：暂停 / 结束前必须先关追踪。"""
    client.post("/api/sessions", json={"mode": "daily", "label": "alone"})
    cmds = client.get("/api/bridge/commands", params={"after": 0}).json()
    assert [c["type"] for c in cmds[-2:]] == ["SET_TRACKING", "GOTO_PRESET"] and cmds[-2]["args"] == {"on": False}
    client.post("/api/bridge/ack", json={"id": cmds[-1]["id"], "ok": True, "code": 0})
    wait_for(lambda: client.get("/api/state").json()["session"]["status"] == "observing")
    client.post("/api/sessions/current/pause")
    cmds = client.get("/api/bridge/commands", params={"after": cmds[-1]["id"]}).json()
    assert [c["type"] for c in cmds] == ["SET_TRACKING", "GOTO_PRESET"] and cmds[1]["args"]["index"] == 3
    client.post("/api/sessions/current/stop")
    cmds = client.get("/api/bridge/commands", params={"after": cmds[-1]["id"]}).json()
    assert [c["type"] for c in cmds] == ["SET_TRACKING", "GOTO_PRESET"]


def test_preview_socket_relays_latest_frame(client):
    import json as _json
    import struct

    head = _json.dumps({"cap": None}).encode()
    jpeg = b"\xff\xd8fake-jpeg\xff\xd9"
    with client.websocket_connect("/ws/frames") as up:
        up.send_bytes(struct.pack(">I", len(head)) + head + jpeg)
        with client.websocket_connect("/ws/preview") as pv:
            assert pv.receive_bytes() == jpeg

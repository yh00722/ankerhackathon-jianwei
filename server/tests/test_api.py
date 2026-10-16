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
    settings.move_settle_s = 0.0
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

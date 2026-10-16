"""看门狗的判断逻辑（用假的服务状态，不真的重启任何东西）。"""

import importlib.util
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("keepalive", Path(__file__).resolve().parents[2] / "scripts" / "keepalive.py")
ka = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ka)


def setup(monkeypatch, up: dict, procs=("zsh scripts/adb-reverse-loop.sh",)):
    calls = {"sh": [], "get": [], "log": []}

    def fake_get(url, timeout=3.0, method="GET"):
        calls["get"].append((method, url))
        if "8000/api/health" in url:
            return {"ok": True} if up["server"] else None
        if "8000/api/state" in url:
            return up["state"]
        if url == ka.CONSOLE:
            return b"<html>" if up["web"] else None
        if url.endswith("9222/json"):
            return up["pages"]
        return {}

    monkeypatch.setattr(ka, "get", fake_get)
    monkeypatch.setattr(ka, "sh", lambda cmd, cwd=None: calls["sh"].append(cmd))
    monkeypatch.setattr(ka, "log", lambda m: calls["log"].append(m))
    monkeypatch.setattr(ka, "_procs", lambda: list(procs))
    monkeypatch.setattr(ka, "adb", lambda *a, **k: "device\n")
    return calls


def state(fps=14.0, connected=True, charging=1, battery=100):
    return {"stream": {"fps_in": fps, "frame_age_s": 0.1 if fps else None, "source": "tap"},
            "bridge": {"connected": connected, "status": {"charging": charging, "battery": battery}}}


CONSOLE_PAGE = [{"type": "page", "id": "A", "url": "http://127.0.0.1:5173/"}]


def tick(w):
    st = w.check_server()
    w.check_web()
    w.check_chrome()
    if st:
        w.check_stream(st)
        w.check_phone(st)


def test_server_restarted_after_two_failed_checks(monkeypatch):
    up = {"server": False, "web": True, "state": None, "pages": CONSOLE_PAGE}
    calls = setup(monkeypatch, up)
    w = ka.Watch(False)
    tick(w)
    assert not calls["sh"]  # 一次没响应先不动（可能只是忙）
    tick(w)
    assert any("run.sh" in c for c in calls["sh"])


def test_web_and_chrome_and_console_page_are_brought_back(monkeypatch):
    up = {"server": True, "web": False, "state": state(), "pages": None}
    calls = setup(monkeypatch, up)
    w = ka.Watch(False)
    tick(w)
    tick(w)
    assert any("npm run dev" in c for c in calls["sh"])
    assert any("start-chrome.sh" in c for c in calls["sh"])
    up["pages"] = [{"type": "page", "id": "E", "url": "http://127.0.0.1:5173/#/elder"}]  # 只剩老人端页
    calls["get"].clear()
    tick(w)
    assert ("PUT", "http://127.0.0.1:9222/json/new?http://127.0.0.1:5173/") in calls["get"]


def test_stream_down_long_reloads_console_once(monkeypatch):
    up = {"server": True, "web": True, "state": state(), "pages": CONSOLE_PAGE}
    calls = setup(monkeypatch, up)
    w = ka.Watch(False)
    tick(w)
    up["state"] = state(fps=0.0)
    tick(w)
    assert any("直播断开" in m for m in calls["log"])
    w.stream_since -= ka.STREAM_RELOAD_S + 1  # 已经断了 5 分钟多
    calls["get"].clear()
    tick(w)
    assert any(u.endswith("json/close/A") for _, u in calls["get"])  # 旧页关掉、开新页
    calls["get"].clear()
    tick(w)
    assert not any("json/close" in u for _, u in calls["get"])  # 10 分钟内不重复
    up["state"] = state()
    tick(w)
    assert any("直播恢复" in m for m in calls["log"])


def test_adb_loop_restarted_and_power_warning_ignores_full_battery(monkeypatch):
    up = {"server": True, "web": True, "state": state(charging=0, battery=100), "pages": CONSOLE_PAGE}
    calls = setup(monkeypatch, up, procs=())
    w = ka.Watch(False)
    tick(w)
    assert any("adb-reverse-loop.sh" in c for c in calls["sh"])
    assert not any("没在充电" in m for m in calls["log"])  # 充满了跳 0 不算
    up["state"] = state(charging=0, battery=80)
    tick(w)
    w.unplugged_since -= 121
    tick(w)
    assert sum("没在充电" in m for m in calls["log"]) == 1


def test_keep_app_front_only_when_asked(monkeypatch):
    up = {"server": True, "web": True, "state": state(connected=False), "pages": CONSOLE_PAGE}
    calls = setup(monkeypatch, up)
    fronted = []
    monkeypatch.setattr(ka, "adb", lambda *a, **k: fronted.append(a) or "device\n")
    w = ka.Watch(False)
    tick(w)
    w.bridge_down_since = time.time() - 61
    tick(w)
    assert not any("am" in a for a in fronted)
    w2 = ka.Watch(True)
    tick(w2)
    w2.bridge_down_since = time.time() - 61
    tick(w2)
    assert any("am" in a for a in fronted)

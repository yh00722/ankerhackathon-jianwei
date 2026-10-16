"""最小化功能测试（自动部分）：不需要人在镜头前，约 1 分钟。会转动摄像头。

用法：python scripts/smoke.py
前提：scripts/start.sh 已启动；控制台已点「一键启动」出画面；手机 App 停在「见微知行」页签。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
results: list[tuple[bool, str, str]] = []


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"：{detail}" if detail else ""))
    return ok


def wait(fn, timeout: float, step: float = 0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def state() -> dict:
    return call("GET", "/api/state")[1]


def command(type_: str, **args) -> dict | None:
    cid = call("POST", "/api/device/command", {"type": type_, "args": args})[1]["id"]
    return wait(lambda: next((c for c in state()["bridge"]["recent"] if c["id"] == cid and c["state"] in ("done", "failed")), None), 15)


def main() -> int:
    print("① 服务与画面")
    code, _ = call("GET", "/api/health")
    if not check("分析服务在线", code == 200):
        return 1
    st = state()
    s = st["stream"]
    check("画面在上传（控制台已出流）", s["fps_in"] >= 8 and (s["frame_age_s"] or 99) < 2,
          f"上传 {s['fps_in']} 帧/秒，处理 {s['fps_done']} 帧/秒，延迟 {s['latency_ms']} ms")

    print("② 设备桥（App SDK）")
    b = st["bridge"]
    check("手机设备桥已连接", b["connected"], "" if b["connected"] else "检查 USB 线、App 是否在「见微知行」页签")
    stt = b["status"]
    check("视频以外的设备信息", bool(stt) and stt.get("online") is not None,
          f"在线 {stt.get('online')}，电量 {stt.get('battery')}%，Wi-Fi {stt.get('wifi_level')} 格")
    for d in ("RIGHT", "LEFT"):
        c = command("PTZ_STEP", dir=d)
        check(f"设备控制：云台{'右' if d == 'RIGHT' else '左'}转一步", bool(c and c["ok"]),
              f"确认 {round(c['latency_ms'])} ms" if c and c["ok"] else str(c))
    c = command("QUERY_EVENTS")
    check("设备事件查询", bool(c and c["ok"]), f"{len(state()['bridge']['events'])} 条" if c and c["ok"] else str(c))

    print("③ 观察会话（会转到日常区 → 墙面）")
    call("POST", "/api/sessions", {"mode": "daily", "label": "alone"})
    s1 = wait(lambda: (x := state()["session"]) and x["status"] == "observing" and x, 25)
    check("开始 → 设备确认后进入「正在观察」", bool(s1 and s1["preset_ok"]), "" if s1 else "25 秒内没进入观察")
    call("POST", "/api/sessions/current/pause")
    s2 = wait(lambda: (x := state()["session"]) and x["status"] == "paused" and x["preset_ok"] is not None and x, 20)
    check("暂停 → 镜头转向墙面并确认", bool(s2 and s2["preset_ok"]))
    call("POST", "/api/sessions/current/stop")
    check("结束", state()["session"]["status"] == "ended")

    print("④ 分享授权（服务端校验）")
    share = call("GET", "/api/share")[1]
    token, before = share["family_token"], share["level"]
    call("PUT", "/api/share", {"level": "none"})
    check("未分享时家属端被拒绝（403）", call("GET", f"/api/family/summary?token={token}")[0] == 403)
    call("PUT", "/api/share", {"level": "summary"})
    code, body = call("GET", f"/api/family/summary?token={token}")
    check("分享后家属端能看到摘要（200）", code == 200 and "disclaimer" in body)
    call("PUT", "/api/share", {"level": "none"})
    check("撤销后立即被拒绝（403）", call("GET", f"/api/family/summary?token={token}")[0] == 403)
    check("错误令牌被拒绝（403）", call("GET", "/api/family/summary?token=wrong")[0] == 403)
    call("PUT", "/api/share", {"level": before})

    failed = [r for r in results if not r[0]]
    print(f"\n结果：{len(results) - len(failed)}/{len(results)} 通过" + ("" if not failed else "；未通过：" + "、".join(r[1] for r in failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

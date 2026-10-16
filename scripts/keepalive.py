"""看门狗：每 20 秒检查一遍演示链路，哪个环节挂了就自动拉起来，并记录直播断开 / 恢复的时间。

它不能让摄像头不断：电池摄像头约每 5 分钟会自己挂断一次直播（省电机制），页面会自动重连；
这里只负责「断了以后一定能接上」，以及页面自己接不上时的兜底。

用法（在 dev/jianwei 下；scripts/start.sh 会自动在后台启动它）：
  python scripts/keepalive.py                 # 前台运行，Ctrl+C 停止
  python scripts/keepalive.py --keep-app-front  # 另外在设备桥断开 1 分钟后，把手机 App 切回前台（会打断正在用手机的人）
日志：server/data/logs/keepalive.log
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT.parent
LOG = ROOT / "server" / "data" / "logs"
CONSOLE = "http://127.0.0.1:5173/"
APP = "com.anker.esiotkit.demo/com.anker.esiotkit.sample.main.ESlotKitMainActivity"

INTERVAL_S = 20
STREAM_RELOAD_S = 300  # 直播断开超过这么久页面还没接上，就把控制台页整个重开
RELOAD_GAP_S = 600  # 两次重开控制台页至少间隔这么久
APP_FRONT_S = 60  # --keep-app-front：设备桥断开超过这么久，把 App 切回前台


def log(msg: str) -> None:
    line = time.strftime("%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    LOG.mkdir(parents=True, exist_ok=True)
    with open(LOG / "keepalive.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get(url: str, timeout: float = 3.0, method: str = "GET"):
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            try:
                return json.loads(body)
            except ValueError:
                return body
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def sh(cmd: str, cwd: Path | None = None) -> None:
    """后台启动，不等它结束（输出写进对应日志）。"""
    subprocess.Popen(["/bin/zsh", "-c", cmd], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def adb(*args: str, timeout: float = 5.0) -> str:
    try:
        env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":" + str(Path.home() / "Library/Android/sdk/platform-tools")}
        return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class Watch:
    def __init__(self, keep_app_front: bool):
        self.keep_app_front = keep_app_front
        self.fail = {"server": 0, "web": 0}
        self.stream_up: bool | None = None
        self.stream_since = time.time()
        self.last_reload = 0.0
        self.bridge_down_since: float | None = None
        self.warned_power = False

    def check_server(self) -> dict | None:
        if get("http://127.0.0.1:8000/api/health") is not None:
            self.fail["server"] = 0
            return get("http://127.0.0.1:8000/api/state")
        self.fail["server"] += 1
        if self.fail["server"] >= 2:
            log("⚠️ 分析服务没有响应，重启")
            sh(f"nohup ./run.sh > {LOG}/server.log 2>&1 &", cwd=ROOT / "server")
            self.fail["server"] = 0
        return None

    def check_web(self) -> None:
        if get(CONSOLE) is not None:
            self.fail["web"] = 0
            return
        self.fail["web"] += 1
        if self.fail["web"] >= 2:
            log("⚠️ 网页服务没有响应，重启")
            sh(f"nohup npm run dev -- --host 127.0.0.1 --port 5173 --strictPort > {LOG}/web.log 2>&1 &", cwd=ROOT / "web")
            self.fail["web"] = 0

    def console_targets(self) -> list[dict] | None:
        ts = get("http://127.0.0.1:9222/json")
        if ts is None:
            return None
        return [t for t in ts if t.get("type") == "page" and t.get("url", "").startswith(CONSOLE)
                and "#/" not in t["url"].replace(CONSOLE, "", 1)[:2]]

    def check_chrome(self) -> None:
        pages = self.console_targets()
        if pages is None:
            log("⚠️ 演示用 Chrome 没在运行，重新打开")
            sh(f'"{DEV}/start-chrome.sh" "{CONSOLE}"')
            return
        if not pages:
            log("⚠️ 控制台页被关掉了，重新打开")
            get("http://127.0.0.1:9222/json/new?" + urllib.parse.quote(CONSOLE, safe=":/"), method="PUT")

    def reload_console(self) -> None:
        pages = self.console_targets() or []
        get("http://127.0.0.1:9222/json/new?" + urllib.parse.quote(CONSOLE, safe=":/"), method="PUT")
        for t in pages:
            get(f"http://127.0.0.1:9222/json/close/{t['id']}")
        self.last_reload = time.time()

    def check_stream(self, st: dict) -> None:
        s = st["stream"]
        up = (s.get("fps_in") or 0) >= 3 and (s.get("frame_age_s") if s.get("frame_age_s") is not None else 99) < 5
        now = time.time()
        if up != self.stream_up:
            dur = now - self.stream_since
            if self.stream_up is None:
                log(f"当前直播：{'在线' if up else '断开'}；设备桥：{'已连接' if st['bridge'].get('connected') else '断开'}")
            else:
                if up:
                    log(f"✅ 直播恢复（断了 {dur:.0f} 秒），来源 {s.get('source')}，{s.get('fps_in')} 帧/秒")
                else:
                    log(f"❌ 直播断开（这次连了 {dur / 60:.1f} 分钟）")
            self.stream_up, self.stream_since = up, now
        if not up and now - self.stream_since > STREAM_RELOAD_S and now - self.last_reload > RELOAD_GAP_S:
            log(f"🔁 直播 {STREAM_RELOAD_S // 60} 分钟没接上，重开控制台页（全新连接）")
            self.reload_console()

    def check_phone(self, st: dict) -> None:
        if not any(p.endswith("adb-reverse-loop.sh") or "adb-reverse-loop.sh" in p for p in _procs()):
            log("⚠️ adb reverse 循环没在跑，重新启动")
            sh(f'nohup "{ROOT}/scripts/adb-reverse-loop.sh" > /dev/null 2>&1 &')
        b = st["bridge"]
        now = time.time()
        if b.get("connected"):
            if self.bridge_down_since is not None:
                log(f"✅ 设备桥恢复（断了 {now - self.bridge_down_since:.0f} 秒）")
            self.bridge_down_since = None
        else:
            if self.bridge_down_since is None:
                self.bridge_down_since = now
                log("❌ 设备桥断开（手机 App 不在前台、熄屏，或 USB 断了）")
            elif self.keep_app_front and now - self.bridge_down_since > APP_FRONT_S and "device" in adb("get-state"):
                log("🔁 把手机 App 切回前台")
                adb("shell", "am", "start", "-n", APP)
                self.bridge_down_since = now  # 一分钟内不重复
        charging = (b.get("status") or {}).get("charging")
        if charging == 0 and not self.warned_power:
            log("⚠️ 摄像头没在充电：电池供电时挂断直播更频繁、重连更慢，建议插上电")
            self.warned_power = True
        elif charging == 1:
            self.warned_power = False

    def run(self) -> None:
        log(f"看门狗启动（每 {INTERVAL_S} 秒检查一次{'，会把手机 App 切回前台' if self.keep_app_front else ''}）")
        while True:
            try:
                st = self.check_server()
                self.check_web()
                self.check_chrome()
                if st:
                    self.check_stream(st)
                    self.check_phone(st)
            except Exception as e:  # 看门狗自己不能挂
                log(f"看门狗出错：{e!r}")
            time.sleep(INTERVAL_S)


def _procs() -> list[str]:
    try:
        return subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=5).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-app-front", action="store_true")
    a = ap.parse_args()
    # 看门狗运行期间不让电脑睡眠（caffeinate 跟着本进程退出，不改系统设置）
    subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        Watch(a.keep_app_front).run()
    except KeyboardInterrupt:
        log("看门狗停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())

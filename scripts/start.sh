#!/bin/zsh
# 一键启动：分析服务（:8000）→ 网页（:5173）→ adb reverse（手机访问本机）→ 演示用 Chrome（控制台）
# 用法：scripts/start.sh        启动全部
#       scripts/start.sh stop   停止服务端和网页
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/server/data/logs"
mkdir -p "$LOG"
source "$ROOT/../env.sh" >/dev/null 2>&1

stop_port() { lsof -ti tcp:$1 -sTCP:LISTEN | xargs -r kill 2>/dev/null; }

if [[ "${1:-}" == "stop" ]]; then
  stop_port 8000; stop_port 5173
  pkill -f "adb-reverse-loop.sh" 2>/dev/null
  echo "已停止"; exit 0
fi

wait_http() {  # 等待地址可访问，最多 $2 秒
  for i in $(seq 1 $2); do curl -s -o /dev/null "$1" && return 0; sleep 1; done
  echo "⚠️  等待超时：$1"; return 1
}

echo "▶ 分析服务"
if ! curl -s -o /dev/null http://127.0.0.1:8000/api/health; then
  (cd "$ROOT/server" && nohup ./run.sh > "$LOG/server.log" 2>&1 &)
  wait_http http://127.0.0.1:8000/api/health 60 && echo "  ✅ http://127.0.0.1:8000"
else
  echo "  ✅ 已在运行"
fi

echo "▶ 网页"
if ! curl -s -o /dev/null http://127.0.0.1:5173/; then
  (cd "$ROOT/web" && nohup npm run dev -- --host 127.0.0.1 --port 5173 --strictPort > "$LOG/web.log" 2>&1 &)
  wait_http http://127.0.0.1:5173/ 60 && echo "  ✅ http://127.0.0.1:5173"
else
  echo "  ✅ 已在运行"
fi

echo "▶ adb reverse（USB 重新插拔后自动重设）"
pkill -f "adb-reverse-loop.sh" 2>/dev/null
nohup "$ROOT/scripts/adb-reverse-loop.sh" > /dev/null 2>&1 &
adb devices | sed -n '2p' | grep -q device && echo "  ✅ 手机已连接" || echo "  ⚠️  没有检测到手机（插上 USB 后会自动生效）"

echo "▶ 演示用 Chrome（独立资料目录，已关闭 WebRTC PQC for DTLS）"
if curl -s -o /dev/null http://127.0.0.1:9222/json/version; then
  echo "  ✅ 已在运行"
else
  "$ROOT/../start-chrome.sh" "http://127.0.0.1:5173/" && echo "  ✅ 已启动，在页面上点「一键启动」连接摄像头"
fi

echo
echo "控制台  http://127.0.0.1:5173/"
echo "老人端  http://127.0.0.1:5173/#/elder（手机 App「见微知行」页签里就是它）"
TOKEN=$(curl -s http://127.0.0.1:8000/api/share | python3 -c "import sys,json; print(json.load(sys.stdin)['family_token'])" 2>/dev/null)
echo "家属端  http://127.0.0.1:5173/#/family?token=$TOKEN"

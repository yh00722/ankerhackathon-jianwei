#!/bin/zsh
# 每 3 秒重设一次 adb reverse：USB 重新插拔后，手机的 localhost:8000 / 5173 会自动恢复映射到本机
source "$(cd "$(dirname "$0")/../.." && pwd)/env.sh" >/dev/null 2>&1
while true; do
  adb reverse tcp:8000 tcp:8000 >/dev/null 2>&1
  adb reverse tcp:5173 tcp:5173 >/dev/null 2>&1
  sleep 3
done

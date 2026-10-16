#!/bin/zsh
# 启动分析服务（127.0.0.1:8000）。JW_ROTATE 等配置见 jianwei/config.py
cd "$(dirname "$0")"
source ../../env.sh >/dev/null 2>&1
exec python -m uvicorn jianwei.app:app --host 127.0.0.1 --port 8000 "$@"

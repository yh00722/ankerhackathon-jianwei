#!/bin/zsh
# 启动分析服务（127.0.0.1:8000）。JW_ROTATE 等配置见 jianwei/config.py
cd "$(dirname "$0")"
source ../../env.sh >/dev/null 2>&1
# 大模型改写措辞的配置（地址、Key、模型）：放在不进 git 的 .env.llm 里，没有就只用模板句
[ -f .env.llm ] && { set -a; source .env.llm; set +a; }
exec python -m uvicorn jianwei.app:app --host 127.0.0.1 --port 8000 "$@"

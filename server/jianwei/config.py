"""运行配置。都可以用环境变量覆盖，方便现场调整机位和阈值。

控制台上改的运行时设置（画面旋转）写进 data/runtime.json，服务重启后还在。
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    db_path: str = os.environ.get("JW_DB", str(ROOT / "data" / "jianwei.db"))
    model_path: str = os.environ.get(
        "JW_MODEL", str(ROOT.parents[1] / "models" / "pose_landmarker_full.task")
    )
    # 训练模式（30 秒坐站要数准）用 heavy：10/16 实测 960 宽每帧 27 ms（full 15 ms），远够 15 帧/秒；
    # 日常模式一开就是几小时，继续用 full 省电。设成空字符串则训练也用 full
    training_model_path: str = os.environ.get(
        "JW_TRAINING_MODEL", str(ROOT.parents[1] / "models" / "pose_landmarker_heavy.task")
    )
    # 摄像头倒装时画面上下颠倒，送进姿态模型前先旋转（0 / 90 / 180 / 270）；控制台上可随时切换
    rotate: int = int(os.environ.get("JW_ROTATE", "0"))
    # 送进模型前把长边缩到这个宽度
    infer_width: int = int(os.environ.get("JW_INFER_WIDTH", "960"))
    camera_sn: str = os.environ.get("JW_CAMERA_SN", "")
    # 预置位编号：1 日常区，2 训练位，3 隐私位（朝墙）
    presets: dict = field(default_factory=lambda: {"daily": 1, "training": 2, "privacy": 3})
    # 「镜头转动中」的判断（engine.camera_moving）：指令状态 + 画面整体位移
    # - 指令发出、还没确认：算转动中，最长 move_timeout_s
    # - 已确认（ACK 只表示接受了指令）：画面开始动了，就等画面停下；确认后 move_wait_s 内画面都没动，
    #   说明本来就在这个位置，不用转
    # - 确认后超过 move_cap_s 只看画面（防止画面误判一直不结束）
    # 实测：ACK 45–250 ms；从下指令到画面里看到转动约 1.0–1.2 秒（视频延迟）
    move_timeout_s: float = 15.0
    move_wait_s: float = float(os.environ.get("JW_MOVE_WAIT", "2.5"))
    move_cap_s: float = 10.0
    # 多久收不到帧算断流
    stream_down_s: float = 2.0
    # 数据保留天数
    retention_days: int = 90
    # 报告里「今天」的分界和显示时间：按比赛所在地（深圳）的北京时间
    tz: str = os.environ.get("JW_TZ", "Asia/Shanghai")


RUNTIME_KEYS = ("rotate",)


def runtime_path(db_path: str) -> Path:
    return Path(db_path).parent / "runtime.json"


def load_runtime(s: Settings, db_path: str) -> None:
    """启动时读回控制台上改过的设置（没有文件就用环境变量 / 默认值）。"""
    try:
        d = json.loads(runtime_path(db_path).read_text())
    except (OSError, ValueError):
        return
    for k in RUNTIME_KEYS:
        if k in d:
            setattr(s, k, type(getattr(s, k))(d[k]))


def save_runtime(s: Settings, db_path: str) -> None:
    p = runtime_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: getattr(s, k) for k in RUNTIME_KEYS}))


settings = Settings()

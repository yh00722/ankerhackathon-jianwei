"""运行配置。都可以用环境变量覆盖，方便现场调整机位和阈值。"""

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
    # 摄像头倒装时画面上下颠倒，送进姿态模型前先旋转（0 / 90 / 180 / 270）
    rotate: int = int(os.environ.get("JW_ROTATE", "180"))
    # 送进模型前把长边缩到这个宽度
    infer_width: int = int(os.environ.get("JW_INFER_WIDTH", "960"))
    camera_sn: str = os.environ.get("JW_CAMERA_SN", "")
    # 预置位编号：1 日常区，2 训练位，3 隐私位（朝墙）
    presets: dict = field(default_factory=lambda: {"daily": 1, "training": 2, "privacy": 3})
    # 指令发出后多久内算「镜头转动中」；收到确认后再留 1.5 秒缓冲
    move_timeout_s: float = 15.0
    move_settle_s: float = 1.5
    # 多久收不到帧算断流
    stream_down_s: float = 2.0
    # 数据保留天数
    retention_days: int = 90


settings = Settings()

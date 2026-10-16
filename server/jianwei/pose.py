"""MediaPipe Pose Landmarker 封装（VIDEO 模式，最多 2 人）。帧解码、旋转、缩放也在这里。"""

from __future__ import annotations

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mpt
from mediapipe.tasks.python import vision

from .features import PoseObs

_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def decode(jpeg: bytes, rotate: int, width: int) -> np.ndarray | None:
    """JPEG → 旋转 → 缩放 → RGB。解码失败返回 None。"""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    if rotate in _ROT:
        img = cv2.rotate(img, _ROT[rotate])
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


class PoseEstimator:
    def __init__(self, model_path: str, num_poses: int = 2):
        opts = vision.PoseLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=num_poses,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._lm = vision.PoseLandmarker.create_from_options(opts)
        self._last_ts = -1

    def detect(self, rgb: np.ndarray, ts_ms: int) -> list[PoseObs]:
        ts = max(int(ts_ms), self._last_ts + 1)  # VIDEO 模式要求时间戳严格递增
        self._last_ts = ts
        r = self._lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        out = []
        for i, lms in enumerate(r.pose_landmarks):
            lm = np.array([[p.x, p.y, p.z, p.visibility] for p in lms], dtype=np.float32)
            world = np.array([[p.x, p.y, p.z] for p in r.pose_world_landmarks[i]], dtype=np.float32)
            out.append(PoseObs(lm, world))
        return out

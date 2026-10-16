"""从 MediaPipe 关键点计算姿态特征（口径见 TASKS.md「口径」）。

特征与摄像头朝向基本无关：
- f1：大腿在竖直方向的投影 ÷ 躯干长（坐约 0–0.4，站约 0.7–1.0）
- hip：3D 髋角（肩–髋–膝），坐约 90–120°，站约 160–180°
- knee：3D 膝角（髋–膝–踝），只在脚踝可见时计算
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# MediaPipe Pose 关键点编号
L_SHOULDER, R_SHOULDER = 11, 12
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
SIDES = ((L_SHOULDER, L_HIP, L_KNEE, L_ANKLE), (R_SHOULDER, R_HIP, R_KNEE, R_ANKLE))

VIS_MIN = 0.5  # 肩、髋、膝可见度低于此值的一侧不参与计算


@dataclass
class PoseObs:
    """一帧里的一个人。lm: (33, 4) 归一化 x, y, z, visibility；world: (33, 3) 米制坐标。"""

    lm: np.ndarray
    world: np.ndarray


@dataclass
class Features:
    f1: float
    hip: float
    knee: float | None
    quality: float  # 参与计算的关键点平均可见度
    hip_center: tuple[float, float]  # 像素坐标
    torso_px: float
    bbox: tuple[float, float, float, float]  # 像素坐标 x0, y0, x1, y1
    side: str  # "left" / "right" / "both"


def angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """以 b 为顶点的夹角（度）。"""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n < 1e-9:
        return float("nan")
    cos = float(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))


def bbox_px(p: PoseObs, w: int, h: int) -> tuple[float, float, float, float]:
    vis = p.lm[:, 3] >= VIS_MIN
    pts = p.lm[vis] if vis.any() else p.lm
    xs, ys = pts[:, 0] * w, pts[:, 1] * h
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def compute(p: PoseObs, w: int, h: int) -> Features | None:
    """算不出来（关键点不可信）时返回 None，调用方据此判为 LOW_QUALITY。"""
    lm, world = p.lm, p.world
    px = lm[:, :2] * np.array([w, h])
    f1s, hips, knees, weights, used = [], [], [], [], []
    for name, (sh, hp, kn, an) in zip(("left", "right"), SIDES):
        vis = min(lm[sh, 3], lm[hp, 3], lm[kn, 3])
        if vis < VIS_MIN:
            continue
        torso = np.linalg.norm(px[sh] - px[hp])
        if torso < 1.0:
            continue
        f1s.append((px[kn, 1] - px[hp, 1]) / torso)
        hips.append(angle_deg(world[sh], world[hp], world[kn]))
        if lm[an, 3] >= VIS_MIN:
            knees.append(angle_deg(world[hp], world[kn], world[an]))
        weights.append(vis)
        used.append(name)
    if not f1s:
        return None
    wts = np.array(weights)
    f1 = float(np.average(f1s, weights=wts))
    hip = float(np.nanmean(hips)) if np.isfinite(hips).any() else float("nan")
    knee = float(np.nanmean(knees)) if knees and np.isfinite(knees).any() else None
    hc = (px[L_HIP] + px[R_HIP]) / 2
    sc = (px[L_SHOULDER] + px[R_SHOULDER]) / 2
    return Features(
        f1=f1,
        hip=hip,
        knee=knee,
        quality=float(wts.mean()),
        hip_center=(float(hc[0]), float(hc[1])),
        torso_px=float(np.linalg.norm(sc - hc)),
        bbox=bbox_px(p, w, h),
        side="both" if len(used) == 2 else used[0],
    )

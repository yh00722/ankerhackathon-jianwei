"""训练动作用的关键点几何。只用看得清的点（可见度 ≥ VIS），算不出来返回 None，由调用方记「没看全」。

坐标：lm 是旋转后画面里的归一化坐标（x 向右、y 向下），world 是 MediaPipe 的 3D 米制坐标。
角度一律用 3D（与摄像头朝向基本无关）；高度、左右位移用画面坐标（换算成像素再比较，避免长宽比失真）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import angle_deg

NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28

SIDE = {
    "left": {"shoulder": L_SHOULDER, "wrist": L_WRIST, "hip": L_HIP, "knee": L_KNEE, "ankle": L_ANKLE},
    "right": {"shoulder": R_SHOULDER, "wrist": R_WRIST, "hip": R_HIP, "knee": R_KNEE, "ankle": R_ANKLE},
}
VIS = 0.5
SIDE_TEXT = {"left": "左腿", "right": "右腿"}


@dataclass
class Body:
    """一帧里目标人的关键点。w、h：画面像素尺寸。"""

    lm: np.ndarray  # (33, 4)
    world: np.ndarray  # (33, 3)
    w: int
    h: int

    def vis(self, *idx: int) -> float:
        return float(min(self.lm[i, 3] for i in idx))

    def ok(self, *idx: int, th: float = VIS) -> bool:
        return self.vis(*idx) >= th

    def px(self, i: int) -> np.ndarray:
        return np.array([self.lm[i, 0] * self.w, self.lm[i, 1] * self.h], dtype=float)

    # ---- 角度 ----

    def knee_angle(self, side: str, th: float = VIS) -> float | None:
        """膝关节夹角（髋–膝–踝，3D）：伸直 ≈ 180°，坐着脚踩地 ≈ 90°。"""
        s = SIDE[side]
        if not self.ok(s["hip"], s["knee"], s["ankle"], th=th):
            return None
        a = angle_deg(self.world[s["hip"]], self.world[s["knee"]], self.world[s["ankle"]])
        return None if a != a else a

    def leg_vis(self, side: str) -> float:
        s = SIDE[side]
        return self.vis(s["hip"], s["knee"], s["ankle"])

    # ---- 长度与位置（像素） ----

    def torso_px(self) -> float | None:
        if not self.ok(L_SHOULDER, R_SHOULDER, L_HIP, R_HIP):
            return None
        sc = (self.px(L_SHOULDER) + self.px(R_SHOULDER)) / 2
        hc = (self.px(L_HIP) + self.px(R_HIP)) / 2
        return float(np.linalg.norm(sc - hc))

    def hip_center(self) -> np.ndarray | None:
        if not self.ok(L_HIP, R_HIP):
            return None
        return (self.px(L_HIP) + self.px(R_HIP)) / 2

    def leg_px(self, side: str) -> float | None:
        """髋到踝的长度（像素），站姿下用来把高度、位移归一化。"""
        s = SIDE[side]
        if not self.ok(s["hip"], s["knee"], s["ankle"]):
            return None
        return float(np.linalg.norm(self.px(s["hip"]) - self.px(s["knee"]))
                     + np.linalg.norm(self.px(s["knee"]) - self.px(s["ankle"])))

    def frontal(self) -> float | None:
        """正对程度：肩宽 ÷ 躯干长。正面约 0.7–1.0，侧面约 0.1–0.4。"""
        t = self.torso_px()
        if not t:
            return None
        return float(np.linalg.norm(self.px(L_SHOULDER) - self.px(R_SHOULDER)) / t)

    def wrists_on_chest(self) -> bool | None:
        """双手是否放在胸前（手腕高于「髋往上 1/4 躯干」那条线）。看不到手腕返回 None。"""
        t = self.torso_px()
        hc = self.hip_center()
        if t is None or hc is None:
            return None
        seen = [i for i in (L_WRIST, R_WRIST) if self.ok(i)]
        if not seen:
            return None
        line = hc[1] - 0.25 * t
        return all(self.px(i)[1] < line for i in seen)

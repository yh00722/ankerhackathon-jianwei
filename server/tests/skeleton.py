"""合成关键点：按关节角度摆出侧面 / 正面的人，生成 MediaPipe 格式的 lm（归一化坐标 + 可见度）与 world（米）。

侧面：人朝画面右边。大腿方向 α 从「竖直向下」往前量（坐着 90°、站着 0°）；膝角 θ 是髋–膝–踝夹角（伸直 180°）；
小腿方向 β = α − (180° − θ)，同样从竖直向下往前量（坐着 θ = 90° 时小腿竖直，θ < 90° 时脚收到椅子下面）。
world 取画面像素 ÷ 400，左右两侧 z 各差 0.2 米，所以 3D 角度等于画面里的角度。
"""

from __future__ import annotations

import math

import numpy as np

from jianwei import features as F
from jianwei.exercises import Obs
from jianwei.features import PoseObs
from jianwei.kin import Body
from jianwei.posture import PostureClassifier

W, H = 960, 540
TORSO, THIGH, SHANK = 140.0, 110.0, 110.0
FPS = 15.0

# MediaPipe 编号
NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 0, 11, 12, 13, 14, 15, 16
L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 23, 24, 25, 26, 27, 28
L_HEEL, R_HEEL, L_FOOT, R_FOOT = 29, 30, 31, 32


def _leg(hip: np.ndarray, alpha: float, theta: float) -> tuple[np.ndarray, np.ndarray]:
    a = math.radians(alpha)
    knee = hip + THIGH * np.array([math.sin(a), math.cos(a)])
    b = math.radians(alpha - (180.0 - theta))
    ankle = knee + SHANK * np.array([math.sin(b), math.cos(b)])
    return knee, ankle


def side(*, sitting: bool = True, knee_l: float = 90.0, knee_r: float = 90.0, thigh_l: float | None = None,
         thigh_r: float | None = None, wrists: str = "chest", hide: tuple[int, ...] = (), vis: float = 0.9,
         x0: float = 420.0) -> Body:
    """侧面的人。hide：要「看不到」的关键点编号（可见度设成 0.1）。"""
    y_hip = 330.0 if sitting else 260.0
    pts: dict[int, np.ndarray] = {}
    for s, (sh, wr, hp, kn, an, el, heel, foot), dx, knee, thigh in (
        ("l", (L_SH, L_WR, L_HIP, L_KN, L_AN, L_EL, L_HEEL, L_FOOT), -6.0, knee_l, thigh_l),
        ("r", (R_SH, R_WR, R_HIP, R_KN, R_AN, R_EL, R_HEEL, R_FOOT), 6.0, knee_r, thigh_r),
    ):
        hip = np.array([x0 + dx, y_hip])
        alpha = thigh if thigh is not None else (90.0 if sitting else 0.0)
        k, a = _leg(hip, alpha, knee)
        shoulder = hip + np.array([0.0, -TORSO])
        pts[hp], pts[kn], pts[an], pts[sh] = hip, k, a, shoulder
        pts[heel], pts[foot] = a + np.array([-8.0, 6.0]), a + np.array([18.0, 8.0])
        if wrists == "chest":
            w = shoulder + np.array([18.0, 45.0])
        elif wrists == "knees":
            w = k + np.array([-6.0, -12.0])
        else:  # 垂在身体两侧
            w = hip + np.array([4.0, 25.0])
        pts[wr], pts[el] = w, (shoulder + w) / 2
    pts[NOSE] = pts[L_SH] + np.array([20.0, -45.0])
    return _body(pts, hide, vis, z={"l": -0.1, "r": 0.1})


def front(*, shift: float = 0.0, hide: tuple[int, ...] = (), vis: float = 0.9, cx: float = 480.0) -> Body:
    """正面的人（面向摄像头：本人的左边在画面右边），骨盆和上身左右平移 shift 像素，脚不动。"""
    y_hip = 260.0
    pts: dict[int, np.ndarray] = {}
    for (sh, hp, kn, an, wr, el), sgn in (((L_SH, L_HIP, L_KN, L_AN, L_WR, L_EL), 1.0),
                                          ((R_SH, R_HIP, R_KN, R_AN, R_WR, R_EL), -1.0)):
        hip = np.array([cx + shift + sgn * 30.0, y_hip])
        ankle = np.array([cx + sgn * 55.0, y_hip + THIGH + SHANK])
        pts[hp], pts[an], pts[kn] = hip, ankle, (hip + ankle) / 2
        pts[sh] = np.array([cx + shift + sgn * 45.0, y_hip - TORSO])
        pts[wr] = pts[sh] + np.array([sgn * 10.0, 120.0])
        pts[el] = (pts[sh] + pts[wr]) / 2
    pts[NOSE] = np.array([cx + shift, y_hip - TORSO - 50.0])
    return _body(pts, hide, vis, z=None)


def _body(pts: dict[int, np.ndarray], hide, vis, z) -> Body:
    lm = np.zeros((33, 4), dtype=np.float32)
    world = np.zeros((33, 3), dtype=np.float32)
    for i, p in pts.items():
        lm[i, 0], lm[i, 1] = p[0] / W, p[1] / H
        lm[i, 3] = 0.1 if i in hide else vis
        world[i, 0], world[i, 1] = p[0] / 400.0, p[1] / 400.0
        if z is not None:
            world[i, 2] = z["l"] if i % 2 == 1 and i >= 11 else z["r"] if i >= 11 else 0.0
    return Body(lm, world, W, H)


class Driver:
    """把合成的 Body 序列喂给真实的特征计算、姿态状态机和训练动作。"""

    def __init__(self, ex, t0: float = 0.0):
        self.ex, self.t = ex, t0
        self.pc = PostureClassifier()
        self.events: list[dict] = [ex.start(t0)]

    def feed(self, body: Body | None, observable: bool = True, reason: str | None = None, near: bool = False):
        ps = None
        if body is not None and observable:
            feat = F.compute(PoseObs(body.lm, body.world), W, H)
            ps = self.pc.update(self.t, feat)
        else:
            self.pc.update(self.t, None)
        o = Obs(self.t, observable and body is not None, reason if body is not None or reason else "NO_PERSON",
                ps, body if observable else None, near)
        self.events += self.ex.update(self.t, o)
        self.t += 1 / FPS

    def hold(self, seconds: float, make, **kw):
        for _ in range(int(round(seconds * FPS))):
            self.feed(make(), **kw)

    def ramp(self, seconds: float, make, a: float, b: float, **kw):
        n = int(round(seconds * FPS))
        for i in range(n):
            self.feed(make(a + (b - a) * i / max(1, n - 1)), **kw)

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == kind]

    def result(self) -> dict | None:
        r = self.of("ex_result")
        return r[-1] if r else None

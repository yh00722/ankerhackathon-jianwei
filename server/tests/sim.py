"""合成姿态序列：用 f1 / 髋角的分段线性轨迹模拟坐、起身、站，驱动真实的姿态状态机和起身状态机。"""

from __future__ import annotations

from jianwei.features import Features
from jianwei.posture import PostureClassifier
from jianwei.rise import RiseDetector

FPS = 15.0
SIT = (0.2, 100.0)
STAND = (0.9, 170.0)


def feat(f1: float, hip: float) -> Features:
    return Features(f1=f1, hip=hip, knee=None, quality=0.9, hip_center=(500.0, 400.0),
                    torso_px=200.0, bbox=(400.0, 100.0, 600.0, 800.0), side="both")


def segments(*segs):
    """segs: (时长秒, 起点(f1,hip), 终点(f1,hip), 选项 dict)。生成 (t, f1, hip, opts) 序列。"""
    t, out = 0.0, []
    for dur, a, b, opts in segs:
        n = max(1, int(round(dur * FPS)))
        for i in range(n):
            k = i / n
            out.append((t, a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, opts or {}))
            t += 1 / FPS
    return out


def run_rise(seq):
    pc, rd, events = PostureClassifier(), RiseDetector(), []
    for t, f1, hip, opts in seq:
        reason = opts.get("reason")
        observable = reason is None
        ps = pc.update(t, feat(f1, hip)) if observable else pc.update(t, None)
        events += rd.update(t, observable, reason, ps if observable else None, opts.get("near", False))
    return events


def rises(events, complete=None):
    out = [e for e in events if e["kind"] == "rise"]
    return out if complete is None else [e for e in out if e["complete"] is complete]

"""模型测速：同一批帧分别用 lite / full / heavy 推理，比较每帧耗时、检出率，以及 f1 和 full 的差。

只在内存里处理（帧来自 /api/debug/bench 现收的一批），用完即丢。
"""

from __future__ import annotations

import statistics
import time

from . import features as F
from .config import ROOT
from .pose import PoseEstimator, decode

MODELS = {name: str(ROOT.parents[1] / "models" / f"pose_landmarker_{name}.task") for name in ("lite", "full", "heavy")}


def run(frames: list[tuple[bytes, float]], models: list[str], rotate: int, width: int) -> dict:
    imgs = [(decode(j, rotate, width), t) for j, t in frames]
    imgs = [(im, t) for im, t in imgs if im is not None]
    out: dict = {"frames": len(imgs), "span_s": round(imgs[-1][1] - imgs[0][1], 2) if len(imgs) > 1 else 0}
    ref: list[float | None] | None = None
    for name in models:
        est = PoseEstimator(MODELS[name])
        ms, f1s = [], []
        for im, t in imgs:
            t0 = time.perf_counter()
            poses = est.detect(im, int(t * 1000))
            ms.append((time.perf_counter() - t0) * 1000)
            h, w = im.shape[:2]
            feats = [f for f in (F.compute(p, w, h) for p in poses) if f is not None]
            big = max(feats, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), default=None)
            f1s.append(big.f1 if big else None)
        warm = ms[3:] or ms  # 前几帧有初始化开销
        q = statistics.quantiles(warm, n=10) if len(warm) >= 10 else [max(warm)] * 9
        r = {"ms_p50": round(statistics.median(warm), 1), "ms_p90": round(q[8], 1),
             "fps_max": round(1000 / statistics.median(warm), 1),
             "detect_rate": round(sum(f is not None for f in f1s) / len(f1s), 2)}
        if name == "full":
            ref = f1s
        out[name] = r
        out[name]["_f1s"] = f1s
    if ref is not None:
        for name in models:
            pairs = [(a, b) for a, b in zip(out[name]["_f1s"], ref) if a is not None and b is not None]
            out[name]["f1_mad_vs_full"] = round(statistics.mean(abs(a - b) for a, b in pairs), 3) if pairs else None
    for name in models:
        out[name].pop("_f1s")
    return out

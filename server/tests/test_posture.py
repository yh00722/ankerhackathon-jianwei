import random
import statistics

from jianwei.calib import thresholds_from
from jianwei.posture import STAND, OneEuro, PostureClassifier
from sim import FPS, feat


def test_one_euro_smooths_jitter_when_still():
    rnd = random.Random(1)
    f = OneEuro(min_cutoff=1.5, beta=2.0)
    raw = [0.2 + rnd.gauss(0, 0.04) for _ in range(200)]
    out = [f(i / FPS, x) for i, x in enumerate(raw)]
    assert statistics.pstdev(out[20:]) < 0.6 * statistics.pstdev(raw[20:])


def test_one_euro_follows_fast_motion():
    f = OneEuro(min_cutoff=1.5, beta=2.0)
    xs = [0.2] * 20 + [0.9] * 10  # 阶跃
    out = [f(i / FPS, x) for i, x in enumerate(xs)]
    assert out[-1] > 0.85  # 0.7 秒内基本跟上


def test_bent_over_is_not_standing_after_calibration():
    # 标定过：这个人站直时髋角 170°。f1 已经很高但弯着腰（髋角 130°）不能算站
    th = thresholds_from({"sit_f1": 0.2, "stand_f1": 0.9, "sit_hip": 100.0, "stand_hip": 170.0})
    pc = PostureClassifier(th)
    states = [pc.update(i / FPS, feat(0.85, 130.0)) for i in range(30)]
    assert not any(s.stand_stable for s in states)
    states = [pc.update((30 + i) / FPS, feat(0.88, 168.0)) for i in range(30)]
    assert states[-1].raw == STAND and states[-1].stand_stable


def test_default_thresholds_keep_lenient_hip_check():
    # 没标定时髋角只在明显矛盾时否决（正面机位髋角估得偏小也能判站）
    pc = PostureClassifier()
    states = [pc.update(i / FPS, feat(0.85, 140.0)) for i in range(30)]
    assert states[-1].stand_stable


def test_calibration_without_hip_separation_ignores_hip():
    th = thresholds_from({"sit_f1": 0.25, "stand_f1": 0.85, "sit_hip": 140.0, "stand_hip": 150.0})
    assert th.stand_hip == 0.0 and th.sit_hip == 999.0
    assert abs(th.sit_f1 - (0.25 + 0.35 * 0.6)) < 1e-9 and abs(th.stand_f1 - (0.25 + 0.7 * 0.6)) < 1e-9

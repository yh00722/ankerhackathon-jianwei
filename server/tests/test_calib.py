from jianwei.calib import Calibrator, thresholds_from

FPS = 14.0


def run(cal, t, dur, f1, hip, jitter=0.0):
    out, n = [], int(dur * FPS)
    for i in range(n):
        x = f1 + (jitter if i % 2 else -jitter)
        out += cal.update(t, True, x, hip)
        t += 1 / FPS
    return t, out


def test_sit_then_stand_gives_thresholds():
    cal = Calibrator()
    cal.start(0.0, 1)
    t, ev = run(cal, 0.0, 3.3, 0.22, 105.0, jitter=0.02)
    assert cal.phase == "stand" and ev[-1]["phase"] == "stand"
    t, _ = run(cal, t, 1.0, 0.5, 140.0)  # 正在站起来：不采
    t, ev = run(cal, t, 3.3, 0.86, 168.0, jitter=0.02)
    assert cal.phase == "done"
    r = ev[-1]["result"]
    assert abs(r["sit_f1"] - 0.22) < 0.03 and abs(r["stand_f1"] - 0.86) < 0.03 and r["preset"] == 1
    th = thresholds_from(r)
    assert th.calibrated and r["sit_f1"] < th.sit_f1 < th.stand_f1 < r["stand_f1"]
    assert 140 < th.stand_hip < 168


def test_moving_restarts_the_window():
    cal = Calibrator()
    cal.start(0.0, 1)
    t, _ = run(cal, 0.0, 2.0, 0.22, 105.0)
    t, _ = run(cal, t, 0.5, 0.45, 120.0, jitter=0.15)  # 还在动
    assert cal.phase == "sit"
    t, _ = run(cal, t, 3.3, 0.22, 105.0)
    assert cal.phase == "stand"


def test_too_small_difference_fails():
    cal = Calibrator()
    cal.start(0.0, 2)
    t, _ = run(cal, 0.0, 3.3, 0.40, 110.0)
    t, ev = run(cal, t, 3.3, 0.58, 160.0)
    assert cal.phase == "failed" and "差别太小" in ev[-1]["error"]


def test_losing_the_person_resets_and_times_out():
    cal = Calibrator()
    cal.start(0.0, 1)
    t = 0.0
    for _ in range(int(46 * FPS)):
        cal.update(t, False, None, None)
        t += 1 / FPS
    assert cal.phase == "failed"

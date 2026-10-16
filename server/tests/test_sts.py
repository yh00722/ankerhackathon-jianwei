from jianwei.posture import PostureClassifier
from jianwei.sts import StsTest
from sim import FPS, SIT, STAND, feat, segments


class Runner:
    """先做准备动作直到进入计时，再从计时开始的时刻往后喂动作。"""

    def __init__(self):
        self.pc, self.st, self.hist, self.t = PostureClassifier(), StsTest(), [], 0.0
        self.events = [self.st.start(0.0)]

    def f1_window(self, t0, t1):
        xs = sorted(f for t, f in self.hist if t0 <= t <= t1)
        return xs[len(xs) // 2] if xs else None

    def feed(self, f1, hip, near=False):
        ps = self.pc.update(self.t, feat(f1, hip))
        if ps.f1 is not None:
            self.hist.append((self.t, ps.f1))
        self.events += self.st.update(self.t, True, None, ps, near, self.f1_window)
        self.t += 1 / FPS

    def prep(self):
        for _, f1, hip, _ in segments((2.0, SIT, SIT, None), (1.0, SIT, STAND, None), (2.0, STAND, STAND, None),
                                      (1.0, STAND, SIT, None)):
            self.feed(f1, hip)
        while self.st.phase != "running":
            self.feed(*SIT)
        self.t_run = self.t

    def after(self, *segs, near_from=None):
        for dt, f1, hip, _ in segments(*segs):
            self.feed(f1, hip, near=near_from is not None and dt >= near_from)
        return [e for e in self.events if e["kind"] == "sts_result"]


def cycles(n, up=1.2, top=0.4, down=1.2, bottom=0.6):
    segs = []
    for _ in range(n):
        segs += [(up, SIT, STAND, None), (top, STAND, STAND, None), (down, STAND, SIT, None), (bottom, SIT, SIT, None)]
    return segs


def test_counts_full_stands_in_30_seconds():
    r = Runner()
    r.prep()
    res = r.after(*cycles(8), (4.0, SIT, SIT, None))  # 8 个 3.4 秒的循环 = 27.2 秒，之后坐着等到 30 秒
    assert res and res[0]["reps"] == 8 and res[0]["valid"] is True


def test_half_way_at_the_bell_counts_one_more():
    r = Runner()
    r.prep()
    # 27.2 + 2.0 = 29.2 秒时开始第 9 次起身，30 秒时已起到 2/3
    res = r.after(*cycles(8), (2.0, SIT, SIT, None), (1.2, SIT, STAND, None), (2.0, STAND, STAND, None))
    assert res and res[0]["reps"] == 9


def test_other_person_makes_result_invalid():
    r = Runner()
    r.prep()
    res = r.after(*cycles(8), (4.0, SIT, SIT, None), near_from=10.0)
    assert res and res[0]["valid"] is False and "OTHER_PERSON" in res[0]["invalid_reasons"]

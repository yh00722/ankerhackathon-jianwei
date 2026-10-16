"""训练动作：用合成人体走真实的特征计算 → 姿态状态机 → 动作计数。"""

from jianwei.exercises import BalanceHold, KneeExtension, KneeFlexion, MarchInPlace, WeightShift
from skeleton import L_AN, L_KN, R_AN, R_KN, Driver, front, side


def sit(knee=90.0, **kw):
    return side(sitting=True, knee_l=knee, knee_r=knee, **kw)


def stand(**kw):
    return side(sitting=False, knee_l=180.0, knee_r=180.0, **kw)


# ---------------- 坐位伸膝 ----------------


def knee_ext_set(d, n, hold=3.2, hide_on=None):
    for i in range(n):
        d.ramp(0.8, lambda a: sit(a), 90, 165)
        hide = (L_AN, R_AN) if hide_on == i else ()
        d.hold(hold, lambda: sit(165, hide=hide))
        d.ramp(0.8, lambda a: sit(a), 165, 90)
        d.hold(1.0, lambda: sit(90))


def test_knee_extension_counts_and_times_holds():
    d = Driver(KneeExtension({"target_reps": 10, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(90))  # 坐好 → 开始
    knee_ext_set(d, 10)
    r = d.result()
    assert r and r["reps"] == 10 and r["completed"] and r["valid"]
    assert r["reps_hold_ok"] == 10 and all(3.0 <= h["hold_s"] <= 4.0 for h in r["holds"])
    assert 160 <= r["angle_max_experimental"]["right"] <= 168  # 实验性角度：看得清时才给


def test_knee_extension_short_hold_counts_but_is_marked():
    d = Driver(KneeExtension({"target_reps": 3, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(90))
    knee_ext_set(d, 3, hold=1.0)
    r = d.result()
    assert r and r["reps"] == 3 and r["reps_hold_ok"] == 0


def test_knee_extension_unseen_rep_is_not_counted():
    d = Driver(KneeExtension({"target_reps": 10, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(90))
    knee_ext_set(d, 3, hide_on=1)  # 第 2 次抬腿时脚踝被挡住
    reps = [e for e in d.of("ex_rep")]
    assert sum(e["counted"] for e in reps) == 2 and sum(not e["counted"] for e in reps) == 1


def test_standing_up_mid_set_is_not_a_rep():
    d = Driver(KneeExtension({"target_reps": 10, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(90))
    knee_ext_set(d, 2)
    d.hold(3.0, stand)  # 站起来（膝盖是直的）
    d.hold(3.0, lambda: sit(90))  # 坐回去：不能把这一下算成一次伸膝
    knee_ext_set(d, 1)
    assert sum(e["counted"] for e in d.of("ex_rep")) == 3
    assert d.ex.stood is True


# ---------------- 坐位屈膝 ----------------


def test_knee_flexion_counts_and_reports_flexion_angle():
    d = Driver(KneeFlexion({"target_reps": 5, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(95))  # 脚平放：膝角 95°（临床屈曲 85°）
    for _ in range(5):
        d.ramp(1.0, lambda a: sit(a), 95, 70)
        d.hold(3.2, lambda: sit(70))
        d.ramp(1.0, lambda a: sit(a), 70, 95)
        d.hold(1.0, lambda: sit(95))
    r = d.result()
    assert r and r["reps"] == 5 and r["reps_hold_ok"] == 5
    assert 105 <= r["flexion_max_experimental"]["right"] <= 112  # 180 − 70 = 110


def test_knee_flexion_hidden_foot_is_not_counted():
    d = Driver(KneeFlexion({"target_reps": 5, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(95))
    d.ramp(1.0, lambda a: sit(a), 95, 70)
    d.hold(3.2, lambda: sit(70, hide=(L_AN, R_AN)))  # 脚收到椅子下面被挡住
    d.ramp(1.0, lambda a: sit(a), 70, 95)
    d.hold(1.0, lambda: sit(95))
    reps = d.of("ex_rep")
    assert reps and not any(e["counted"] for e in reps)


# ---------------- 站立平衡 ----------------


def test_balance_hold_reaches_target():
    d = Driver(BalanceHold({"variant": "together", "target_s": 10.0, "attempts": 3}))
    d.hold(12.0, stand)
    r = d.result()
    assert r and r["best_s"] == 10.0 and r["attempts"][0]["ended_by"] == "target"


def test_balance_hold_ends_when_feet_move_then_next_attempt():
    d = Driver(BalanceHold({"variant": "tandem", "target_s": 10.0, "attempts": 2}))
    d.hold(1.0, stand)
    d.hold(6.0, stand)
    d.hold(1.0, lambda: stand(x0=470.0))  # 整个人挪了 50 像素（约 0.23 个腿长）：脚动了
    first = d.of("ex_attempt")[0]
    assert first["ended_by"] == "moved" and 5.5 <= first["hold_s"] <= 7.5
    d.hold(12.0, lambda: stand(x0=470.0))
    r = d.result()
    assert r and len(r["attempts"]) == 2 and r["best_s"] == 10.0


def test_single_leg_stance_ends_when_foot_comes_down():
    def one_leg():
        return side(sitting=False, knee_l=100.0, knee_r=180.0)  # 左脚向后抬起

    d = Driver(BalanceHold({"variant": "single", "target_s": 10.0, "attempts": 1}))
    d.hold(1.0, stand)  # 两脚着地：还没开始
    assert d.ex.phase == "prep"
    d.hold(7.0, one_leg)
    d.hold(1.0, stand)  # 放下
    a = d.of("ex_attempt")[0]
    assert a["ended_by"] == "foot_down" and 6.0 <= a["hold_s"] <= 7.5
    assert d.ex.self_report("yes")["support"] == "yes"


# ---------------- 2 分钟原地抬膝 ----------------


def test_march_in_place_counts_right_knee():
    d = Driver(MarchInPlace({"duration_s": 20.0}))
    while d.ex.phase != "running":  # 站稳量目标高度 → 倒数 3 秒
        d.feed(stand())
        assert d.t < 10
    for _ in range(20):  # 每秒左右各抬一次
        d.ramp(0.25, lambda a: side(sitting=False, knee_l=180, knee_r=180 - a, thigh_r=a), 0, 70)
        d.ramp(0.25, lambda a: side(sitting=False, knee_l=180, knee_r=180 - a, thigh_r=a), 70, 0)
        d.ramp(0.25, lambda a: side(sitting=False, knee_r=180, knee_l=180 - a, thigh_l=a), 0, 70)
        d.ramp(0.25, lambda a: side(sitting=False, knee_r=180, knee_l=180 - a, thigh_l=a), 70, 0)
    d.hold(1.0, stand)
    r = d.result()
    assert r and 19 <= r["reps"] <= 20 and 19 <= r["by_side"]["left"] <= 20


# ---------------- 扶台面转移重心 ----------------


def test_weight_shift_counts_sides_in_front_view():
    d = Driver(WeightShift({"target_reps": 6, "duration_s": 120.0}))
    d.hold(1.0, front)
    for _ in range(3):
        d.ramp(1.0, lambda x: front(shift=x), 0, -40)
        d.ramp(1.0, lambda x: front(shift=x), -40, 0)
        d.ramp(1.0, lambda x: front(shift=x), 0, 40)
        d.ramp(1.0, lambda x: front(shift=x), 40, 0)
    r = d.result()
    assert r and r["reps"] == 6 and r["by_side"] == {"left": 3, "right": 3}


def test_weight_shift_needs_front_view():
    d = Driver(WeightShift({"target_reps": 6}))
    d.hold(3.0, stand)  # 侧面
    assert d.ex.phase == "prep" and any("正面" in e["text"] for e in d.of("ex_note"))


def test_other_person_makes_set_invalid():
    d = Driver(KneeExtension({"target_reps": 2, "hold_s": 3.0, "side": "right"}))
    d.hold(1.5, lambda: sit(90))
    d.ramp(0.8, lambda a: sit(a), 90, 165, near=True)
    d.hold(3.2, lambda: sit(165))
    d.ramp(0.8, lambda a: sit(a), 165, 90)
    d.hold(1.0, lambda: sit(90))
    knee_ext_set(d, 1)
    r = d.result()
    assert r and r["valid"] is False and "OTHER_PERSON" in r["invalid_reasons"]


def test_unused_imports_are_real_landmarks():
    assert (L_KN, R_KN) == (25, 26)

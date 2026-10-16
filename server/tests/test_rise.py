from sim import SIT, STAND, ease_10_90, rises, run_rise, segments


def test_complete_rise_counts_and_times():
    ev = run_rise(segments((2.0, SIT, SIT, None), (1.5, SIT, STAND, None), (1.5, STAND, STAND, None)))
    done = rises(ev, True)
    assert len(done) == 1 and not rises(ev, False)
    # p 从 0.1 到 0.9 的线性段约 1.2 秒（容许一两帧和平滑带来的偏差）
    assert 1.0 <= done[0]["duration_s"] <= 1.45
    assert done[0]["other_person"] is False


def test_gap_during_rise_is_incomplete_and_not_counted():
    ev = run_rise(segments((2.0, SIT, SIT, None), (0.5, SIT, (0.45, 130), None),
                           (0.6, (0.45, 130), (0.7, 155), {"reason": "NO_PERSON"}),
                           (0.6, (0.7, 155), STAND, None), (1.5, STAND, STAND, None)))
    assert not rises(ev, True)
    bad = rises(ev, False)
    assert len(bad) == 1 and bad[0]["reason"] == "NO_PERSON"


def test_camera_moving_during_rise_is_incomplete():
    ev = run_rise(segments((2.0, SIT, SIT, None), (0.6, SIT, (0.5, 135), None),
                           (0.07, (0.5, 135), (0.5, 135), {"reason": "CAMERA_MOVING"}),
                           (0.8, (0.5, 135), STAND, None), (1.5, STAND, STAND, None)))
    assert not rises(ev, True)
    assert [e["reason"] for e in rises(ev, False)] == ["CAMERA_MOVING"]


def test_appearing_already_standing_records_nothing():
    ev = run_rise(segments((1.0, STAND, STAND, {"reason": "NO_PERSON"}), (3.0, STAND, STAND, None)))
    assert rises(ev) == []


def test_seated_then_gap_then_standing_is_incomplete():
    ev = run_rise(segments((2.0, SIT, SIT, None), (3.0, SIT, STAND, {"reason": "NO_PERSON"}),
                           (2.0, STAND, STAND, None)))
    assert not rises(ev, True)
    assert [e["reason"] for e in rises(ev, False)] == ["NO_PERSON"]
    bouts = [e for e in ev if e["kind"] == "sit_bout"]
    assert bouts and bouts[0]["truncated"] is True


def test_two_rises_with_sitting_between():
    seq = segments((2.0, SIT, SIT, None), (1.5, SIT, STAND, None), (1.5, STAND, STAND, None),
                   (1.5, STAND, SIT, None), (2.0, SIT, SIT, None), (1.5, SIT, STAND, None),
                   (1.5, STAND, STAND, None))
    assert len(rises(run_rise(seq), True)) == 2


def test_other_person_near_is_flagged():
    ev = run_rise(segments((2.0, SIT, SIT, None), (1.5, SIT, STAND, {"near": True}), (1.5, STAND, STAND, None)))
    done = rises(ev, True)
    assert len(done) == 1 and done[0]["other_person"] is True


def test_aborted_attempt_is_not_counted():
    ev = run_rise(segments((2.0, SIT, SIT, None), (0.8, SIT, (0.55, 140), None), (0.8, (0.55, 140), SIT, None),
                           (2.0, SIT, SIT, None)))
    assert rises(ev) == []


def test_standing_sway_does_not_double_count():
    seq = segments((2.0, SIT, SIT, None), (1.5, SIT, STAND, None), (1.0, STAND, (0.6, 150), None),
                   (1.0, (0.6, 150), STAND, None), (2.0, STAND, STAND, None))
    assert len(rises(run_rise(seq), True)) == 1


def test_slow_rise_duration_is_not_underestimated():
    # 术后起身慢（4 秒）：要等站稳再取站姿基线，否则用时会算短一半
    for seed in range(5):
        ev = run_rise(segments((3.0, SIT, SIT, None), (4.0, SIT, STAND, None), (3.0, STAND, STAND, None),
                               ease=True, noise=0.02, seed=seed))
        done = rises(ev, True)
        assert len(done) == 1
        assert abs(done[0]["duration_s"] - ease_10_90(4.0)) <= 0.4


def test_brief_jitter_while_seated_does_not_split_sitting():
    # 坐着时姿态抖了一下（判成过渡）又回到坐：不算起身，这段坐也不被切成两段
    seq = segments((3.0, SIT, SIT, None), (0.3, (0.55, 125), (0.55, 125), None), (3.0, SIT, SIT, None),
                   (1.5, SIT, STAND, None), (2.0, STAND, STAND, None))
    ev = run_rise(seq)
    assert len(rises(ev, True)) == 1
    bouts = [e for e in ev if e["kind"] == "sit_bout"]
    assert len(bouts) == 1 and bouts[0]["duration_s"] >= 6.0

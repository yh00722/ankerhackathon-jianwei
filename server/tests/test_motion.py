"""画面位移检测 + 「镜头转动中」判断（指令状态 × 画面）。"""

import numpy as np

from jianwei.bridge import Bridge
from jianwei.config import Settings
from jianwei.engine import Engine
from jianwei.motion import GlobalMotion

FPS = 14.0


def scene(seed=0, w=960, h=540):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8)
    return np.kron(small, np.ones((8, 8, 1), dtype=np.uint8))  # 有纹理的「房间」


def test_static_scene_is_not_moving():
    m, img = GlobalMotion(), scene()
    for i in range(20):
        noisy = np.clip(img.astype(int) + np.random.default_rng(i).integers(-6, 7, img.shape), 0, 255).astype(np.uint8)
        m.update(i / FPS, noisy)
    assert not m.moving(20 / FPS) and m.last_motion_t is None


def test_pan_is_detected_then_stops():
    m, big = GlobalMotion(), scene(w=1400)
    t = 0.0
    for i in range(10):  # 镜头每帧平移 20 像素（约 2 像素 / 小图）
        m.update(t, big[:, i * 20:i * 20 + 960])
        t += 1 / FPS
    assert m.moving(t) and m.sustained(t)
    for _ in range(10):  # 停下
        m.update(t, big[:, 200:1160])
        t += 1 / FPS
    assert not m.moving(t)


def test_person_moving_is_masked():
    m, img = GlobalMotion(), scene()
    t = 0.0
    for i in range(15):  # 背景不动，一个人（亮块）在画面里往右走
        f = img.copy()
        x = 300 + i * 25
        f[150:500, x:x + 150] = 250
        m.update(t, f, [(x, 150, x + 150, 500)])
        t += 1 / FPS
    assert m.last_motion_t is None


def test_large_person_near_camera_partly_masked_is_not_camera_motion():
    # 10/16 晚的误报场景：人离镜头很近、占画面三分之一，关键点框只盖住身体的大部分
    m, img = GlobalMotion(), scene()
    t = 0.0
    for i in range(20):
        f = img.copy()
        x = 250 + int(40 * np.sin(i / 2))  # 左右晃动、比划
        f[60:540, x:x + 330] = 200 + (i % 3) * 10
        m.update(t, f, [(x + 40, 120, x + 290, 540)])  # 框比人小
        t += 1 / FPS
    assert not m.sustained(t)


class FakeStore:
    def log_device(self, *a, **k):
        pass

    def calibration(self, preset):
        return None


def engine():
    s = Settings()
    s.move_wait_s, s.move_cap_s, s.move_timeout_s = 2.5, 10.0, 15.0
    return Engine(s, FakeStore(), Bridge(FakeStore()))


def feed(eng, t0, t1, frames):
    """frames(t) -> 图像；按 14 帧/秒在 [t0, t1) 内喂给位移检测。"""
    t = t0
    while t < t1:
        eng.motion.update(t, frames(t))
        t += 1 / FPS


def test_pending_command_counts_as_moving():
    eng = engine()
    c = eng.bridge.enqueue("GOTO_PRESET", index=1)
    t0 = eng.bridge.last_move()["_mono"]
    assert eng.camera_moving(t0 + 0.1) and eng.camera_moving(t0 + 14.0)
    assert not eng.camera_moving(t0 + 15.5)
    assert c["type"] == "GOTO_PRESET"


def test_acked_without_visible_motion_ends_after_wait():
    eng = engine()
    c = eng.bridge.enqueue("GOTO_PRESET", index=1)
    eng.bridge.ack(c["id"], True, 0, None, 80)
    ta = eng.bridge.last_move()["ack_mono"]
    img = scene()
    feed(eng, ta, ta + 3.0, lambda t: img)  # 本来就在这个位置：画面一直没动
    assert eng.camera_moving(ta + 2.0)
    assert not eng.camera_moving(ta + 2.6)


def test_acked_with_visible_motion_ends_when_picture_settles():
    eng = engine()
    c = eng.bridge.enqueue("GOTO_PRESET", index=1)
    eng.bridge.ack(c["id"], True, 0, None, 80)
    ta = eng.bridge.last_move()["ack_mono"]
    big = scene(w=2400)
    # 确认后 1 秒画面开始转，转 4 秒（超过原来固定的 4 秒缓冲），然后停
    pos = lambda t: int(min(max(t - ta - 1.0, 0.0), 4.0) * 250)  # noqa: E731
    feed(eng, ta, ta + 7.0, lambda t: big[:, pos(t):pos(t) + 960])
    assert eng.camera_moving(ta + 4.8)  # 还在转：原来固定 4 秒会误判为已停
    feed(eng, ta + 7.0, ta + 8.0, lambda t: big[:, 1000:1960])
    assert not eng.camera_moving(ta + 8.0)


def test_unsolicited_sustained_motion_counts_and_blip_does_not():
    eng = engine()  # 没有我们下的指令
    big = scene(w=2400)
    feed(eng, 0.0, 0.2, lambda t: big[:, 0:960])
    eng.motion.update(0.2, big[:, 30:990])  # 偶发一帧跳动
    assert not eng.camera_moving(0.25)
    feed(eng, 1.0, 2.0, lambda t: big[:, int((t - 1.0) * 300):int((t - 1.0) * 300) + 960])  # 有人手动转 / 自动追踪
    assert eng.camera_moving(2.0)

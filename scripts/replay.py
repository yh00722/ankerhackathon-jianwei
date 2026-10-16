"""离线回放调参：读调试记录（server/data/debug/*.jsonl），用不同阈值重跑姿态 + 起身状态机。

调试记录只有数字（f1、髋角、不可观测原因…），见 server/jianwei/debugrec.py。

用法（在 dev/jianwei 下）：
  python scripts/replay.py server/data/debug/20261016-201500.jsonl            # 默认参数重跑，列出起身事件
  python scripts/replay.py FILE --calib 0.22,0.86,105,168                      # 用标定值（坐 f1, 站 f1[, 坐髋角, 站髋角]）
  python scripts/replay.py FILE --sweep --expect 20                            # 扫参数，按「完整起身次数离 20 次多远」排序
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from jianwei.calib import thresholds_from  # noqa: E402
from jianwei.features import Features  # noqa: E402
from jianwei.posture import PostureClassifier, Thresholds  # noqa: E402
from jianwei.rise import RiseConfig, RiseDetector  # noqa: E402


def load(path: str) -> tuple[list[dict], list[dict]]:
    frames, events = [], []
    for line in open(path, encoding="utf-8"):
        d = json.loads(line)
        (events if "ev" in d else frames).append(d)
    return frames, events


def feat(d: dict) -> Features | None:
    if d.get("f1r") is None:
        return None
    hip = d.get("hipr")
    return Features(f1=d["f1r"], hip=float("nan") if hip is None else hip, knee=d.get("kr"), quality=d.get("q") or 0.0,
                    hip_center=(0.0, 0.0), torso_px=1.0, bbox=(0.0, 0.0, 1.0, 1.0), side="both")


def run(frames: list[dict], th: Thresholds, rc: RiseConfig) -> list[dict]:
    pc, rd, out = PostureClassifier(th), RiseDetector(rc), []
    prev_obs = None
    for d in frames:
        if d.get("st") != "observing" or d.get("mode") != "daily":
            if prev_obs is not None:  # 会话中断：状态机从头来
                pc.reset()
                rd.reset()
                prev_obs = None
            continue
        reason = d.get("r")
        observable = reason is None
        ps = pc.update(d["t"], feat(d) if observable else None)
        out += rd.update(d["t"], observable, reason, ps if observable else None, bool(d.get("near")))
        prev_obs = observable
    return out


def dur(e: dict) -> str:
    return "" if e["duration_s"] is None else f"，用时 {e['duration_s']} 秒"


def summary(evs: list[dict]) -> dict:
    rises = [e for e in evs if e["kind"] == "rise"]
    done = [e for e in rises if e["complete"]]
    durs = [e["duration_s"] for e in done if e["duration_s"] is not None]
    reasons: dict[str, int] = {}
    for e in rises:
        if not e["complete"]:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    return {"complete": len(done), "incomplete": len(rises) - len(done), "reasons": reasons,
            "dur_med": round(statistics.median(durs), 2) if durs else None,
            "sit_bouts": sum(e["kind"] == "sit_bout" for e in evs)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--calib", help="坐 f1,站 f1[,坐髋角,站髋角]")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--expect", type=int, help="真实的完整起身次数（人数出来的）")
    a = ap.parse_args()

    frames, recorded = load(a.file)
    obs = [d for d in frames if d.get("st") == "observing"]
    print(f"{len(frames)} 帧（观察中 {len(obs)} 帧，{(obs[-1]['t'] - obs[0]['t']) / 60:.1f} 分钟）" if obs else f"{len(frames)} 帧")
    rec_rises = [d["ev"] for d in recorded if d["ev"].get("kind") == "rise"]
    print(f"记录时实际产生：完整 {sum(e['complete'] for e in rec_rises)} 次，没看全 {sum(not e['complete'] for e in rec_rises)} 次")

    base = Thresholds()
    if a.calib:
        v = [float(x) for x in a.calib.split(",")]
        base = thresholds_from({"sit_f1": v[0], "stand_f1": v[1], "sit_hip": v[2] if len(v) > 2 else None,
                                "stand_hip": v[3] if len(v) > 3 else None})
    evs = run(frames, base, RiseConfig())
    print("当前参数重跑：", summary(evs))
    for e in evs:
        if e["kind"] == "rise":
            print(f"  t={e['t_end']:.1f} {'完整' if e['complete'] else '没看全 ' + str(e['reason'])}"
                  f"{dur(e)}")

    if a.sweep:
        rows = []
        for sit, stand, ss, st in itertools.product((-0.05, 0.0, 0.05), (-0.05, 0.0, 0.05), (0.2, 0.3, 0.5), (0.4, 0.6, 0.8)):
            th = replace(base, sit_f1=base.sit_f1 + sit, stand_f1=base.stand_f1 + stand, stand_stable_s=ss, sit_stable_s=st)
            sm = summary(run(frames, th, RiseConfig()))
            rows.append((th, sm))
        if a.expect is not None:
            rows.sort(key=lambda r: (abs(r[1]["complete"] - a.expect), r[1]["incomplete"]))
        print("\n坐<  站>  站稳 坐稳 | 完整 没看全 用时中位 | 没看全原因")
        for th, sm in rows[:15]:
            print(f"{th.sit_f1:.2f} {th.stand_f1:.2f} {th.stand_stable_s:.1f}  {th.sit_stable_s:.1f}  | "
                  f"{sm['complete']:4d} {sm['incomplete']:5d}  {sm['dur_med']} | {sm['reasons']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

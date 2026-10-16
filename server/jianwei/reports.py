"""本人报告与家属摘要。数字由程序计算；措辞只用「看到 / 记录到 / 没看全 / 没看到」，不做好坏判断。"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import settings

from .store import Store

LABEL = {"alone": "我一个人", "companion": "有人陪"}
DISCLAIMER = "这是观察到的事实，不代表可以延长独处时间；疼痛、伤口等情况请联系医生。"


def fmt_dur(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s} 秒"
    m = s // 60
    if m < 60:
        return f"{m} 分钟"
    return f"{m // 60} 小时 {m % 60} 分" if m % 60 else f"{m // 60} 小时"


def tz() -> ZoneInfo:
    return ZoneInfo(settings.tz)


def day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, tz())
    return datetime(d.year, d.month, d.day, tzinfo=tz()).timestamp()


def session_stats(store: Store, sid: int, now: float) -> dict:
    iv = store.all("SELECT observable, reason, t_start, t_end FROM intervals WHERE session_id = ?", (sid,))
    on_s = sum(r["t_end"] - r["t_start"] for r in iv)
    obs_s = sum(r["t_end"] - r["t_start"] for r in iv if r["observable"])
    unobs: dict[str, float] = {}
    for r in iv:
        if not r["observable"]:
            unobs[r["reason"]] = unobs.get(r["reason"], 0.0) + r["t_end"] - r["t_start"]
    rises = store.all("SELECT * FROM rise_events WHERE session_id = ? ORDER BY t_start", (sid,))
    done = [r for r in rises if r["complete"]]
    durs = [r["duration_s"] for r in done if r["duration_s"]]
    incomplete: dict[str, int] = {}
    for r in rises:
        if not r["complete"]:
            incomplete[r["reason"]] = incomplete.get(r["reason"], 0) + 1
    bouts = store.all("SELECT t_start, t_end, truncated FROM sit_bouts WHERE session_id = ?", (sid,))
    longest = max(bouts, key=lambda b: b["t_end"] - b["t_start"], default=None)
    return {
        "on_s": round(on_s, 1), "observable_s": round(obs_s, 1),
        "coverage": round(obs_s / on_s, 3) if on_s > 0 else None,
        "unobservable_by_reason": {k: round(v, 1) for k, v in unobs.items()},
        "rises_complete": len(done),
        "rise_median_s": round(statistics.median(durs), 2) if durs else None,
        "rises_incomplete": sum(incomplete.values()),
        "incomplete_by_reason": incomplete,
        "other_person": sum(1 for r in rises if r["other_person"]),
        "assist_yes": sum(1 for r in rises if r["assist"] == "yes"),
        "pending_confirm": [r["id"] for r in rises if r["other_person"] and r["assist"] is None],
        "longest_sit_s": round(longest["t_end"] - longest["t_start"], 1) if longest else None,
        "longest_sit_truncated": bool(longest["truncated"]) if longest else False,
    }


def sentence(st: dict, label: str | None) -> str:
    if not st["on_s"]:
        return "这段时间还没有开始观察。"
    who = f"{LABEL.get(label, label)}：" if label else ""
    s = f"{who}开了 {fmt_dur(st['on_s'])}，其中 {fmt_dur(st['observable_s'])}看得清。"
    if st["rises_complete"]:
        s += f"看到完整站起来 {st['rises_complete']} 次"
        s += f"，离座到站直用时中位数 {st['rise_median_s']:.1f} 秒。" if st["rise_median_s"] else "。"
    else:
        s += "这段时间没有看到完整的起身。"
    if st["rises_incomplete"]:
        s += f"另有 {st['rises_incomplete']} 次没看全，没有计入。"
    return s


def sts_records(store: Store) -> list[dict]:
    rows = store.all("SELECT * FROM trainings WHERE kind = 'sts30' ORDER BY started_at")
    for r in rows:
        r["invalid_reasons"] = json.loads(r["invalid_reasons"] or "[]")
        r["conditions"] = json.loads(r["conditions"] or "{}")
    return rows


def sts_compare(store: Store) -> dict | None:
    """最近一次 30 秒坐站，与同条件的上一次有效记录并列。不判断变好或变差。"""
    rows = sts_records(store)
    if not rows:
        return None
    last = rows[-1]
    prev = next((r for r in reversed(rows[:-1]) if r["valid"] and r["conditions"] == last["conditions"]), None)
    return {"last": _sts_pub(last), "previous_same_condition": _sts_pub(prev) if prev else None,
            "history": [_sts_pub(r) for r in rows[-6:]]}


def _sts_pub(r: dict) -> dict:
    return {"id": r["id"], "at": r["started_at"], "reps": r["reps"], "valid": bool(r["valid"]),
            "invalid_reasons": r["invalid_reasons"], "conditions": r["conditions"], "is_sample": bool(r["is_sample"])}


def elder_today(store: Store, now: float | None = None) -> dict:
    now = now or time.time()
    sessions = store.all("SELECT * FROM sessions WHERE started_at >= ? AND is_sample = 0 ORDER BY started_at",
                         (day_start(now),))
    out = []
    for s in sessions:
        st = session_stats(store, s["id"], now)
        out.append({"id": s["id"], "mode": s["mode"], "label": s["label"], "status": s["status"],
                    "started_at": s["started_at"], "ended_at": s["ended_at"], **st,
                    "sentence": sentence(st, s["label"] if s["mode"] == "daily" else None)})
    daily = [x for x in out if x["mode"] == "daily"]
    total = {k: sum(x[k] for x in daily) for k in ("on_s", "observable_s", "rises_complete", "rises_incomplete")}
    return {"date": datetime.fromtimestamp(now, tz()).strftime("%Y-%m-%d"), "sessions": out, "total": total,
            "sts": sts_compare(store)}


def family_summary(store: Store, now: float | None = None) -> dict:
    """「一句话 + 数字」级别的家属摘要。分有人陪和独处两个时段；不附任何画面。"""
    now = now or time.time()
    week = now - 7 * 86400
    sessions = store.all("SELECT * FROM sessions WHERE started_at >= ? AND is_sample = 0", (week,))
    by_label: dict[str, dict] = {}
    for s in sessions:
        if s["mode"] != "daily":
            continue
        st = session_stats(store, s["id"], now)
        agg = by_label.setdefault(s["label"], {"on_s": 0.0, "observable_s": 0.0, "rises_complete": 0,
                                               "rises_incomplete": 0, "assist_yes": 0})
        for k in agg:
            agg[k] += st[k]
    trained_days = {datetime.fromtimestamp(r["started_at"], tz()).date()
                    for r in sts_records(store) if r["started_at"] >= week and not r["is_sample"]}
    parts = []
    for label in ("alone", "companion"):
        a = by_label.get(label)
        if a and a["on_s"]:
            p = (f"{LABEL[label]}的时段：开了 {fmt_dur(a['on_s'])}，看得清 {fmt_dur(a['observable_s'])}，"
                 f"看到完整站起来 {a['rises_complete']} 次")
            if label == "companion" and a["assist_yes"]:
                p += f"，其中确认有人扶 {a['assist_yes']} 次"
            if a["rises_incomplete"]:
                p += f"；另有 {a['rises_incomplete']} 次没看全，没有计入"
            parts.append(p + "。")
    cmp_ = sts_compare(store)
    sts_line = None
    if cmp_ and cmp_["last"]["valid"]:
        last, prev = cmp_["last"], cmp_["previous_same_condition"]
        sts_line = f"30 秒坐站：最近一次 {last['reps']} 次" + ("（示例数据）" if last["is_sample"] else "")
        if prev:
            sts_line += f"，上次同条件 {prev['reps']} 次" + ("（示例数据）" if prev["is_sample"] else "")
        sts_line += "。"
    return {
        "range": [week, now],
        "trained_days": len(trained_days),
        "by_label": {k: {kk: round(vv, 1) if isinstance(vv, float) else vv for kk, vv in v.items()}
                     for k, v in by_label.items()},
        "lines": parts + ([sts_line] if sts_line else []),
        "sts": cmp_,
        "disclaimer": DISCLAIMER,
    }

"""本人报告、家属摘要、康复师趋势。数字由程序计算；措辞只用「看到 / 记录到 / 没看全 / 没看到」，不做好坏判断。

谁看什么（Notion「功能细节」第五节）：
- 本人：全部，包括每个训练动作的次数、保持秒数、实验性角度；
- 家属：本周练了几天、日常活动（分有人陪 / 独处）、30 秒坐站与上次同条件并列；
- 康复师：按天的日常活动与观测覆盖、每个动作的历史（分左右腿），只把同条件的记录放在一起。
"""

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


EX_NAME = {"sts30": "30 秒坐站", "knee_ext": "坐位伸膝", "knee_flex": "坐位屈膝", "balance": "站立平衡",
           "march2min": "2 分钟原地抬膝", "weight_shift": "扶台面转移重心"}


def training_rows(store: Store, since: float = 0.0, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM trainings WHERE started_at >= ?" + (" AND kind = ?" if kind else "") + " ORDER BY started_at"
    rows = store.all(sql, (since, kind) if kind else (since,))
    for r in rows:
        r["invalid_reasons"] = json.loads(r["invalid_reasons"] or "[]")
        r["conditions"] = json.loads(r["conditions"] or "{}")
        r["details"] = json.loads(r.get("details") or "{}")
    return rows


def exercise_line(r: dict) -> str:
    """训练记录的一句话（本人和康复师看）。"""
    k, d, n = r["kind"], r["details"], r["reps"]
    name = EX_NAME.get(k, k)
    if k == "sts30":
        s = f"{name}：{n} 次"
    elif k in ("knee_ext", "knee_flex"):
        s = f"{name}：{n} 次"
        if d.get("hold_mean_s") is not None:
            s += f"，平均保持 {d['hold_mean_s']:.1f} 秒"
        sides = d.get("by_side") or {}
        if len(sides) == 2:
            s += f"（左 {sides.get('left', 0)}、右 {sides.get('right', 0)}）"
    elif k == "balance":
        best = d.get("best_s")
        s = f"{name}（{_variant(d.get('variant'))}）：最长保持 {best:.1f} 秒" if best is not None else f"{name}：没有完整看到的保持"
    elif k == "march2min":
        s = f"{name}：右膝 {n} 次"
    else:
        sides = d.get("by_side") or {}
        s = f"{name}：{n} 次（左 {sides.get('left', 0)}、右 {sides.get('right', 0)}）"
    if not r["valid"]:
        s += "（无效，不进入趋势）"
    return s + "。"


def _variant(v: str | None) -> str:
    return {"together": "并脚站", "semi_tandem": "半前后脚站", "tandem": "前后脚站", "single": "单脚站"}.get(v or "", "站立")


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
    ex = [r for r in training_rows(store, day_start(now)) if r["kind"] != "sts30" and not r["is_sample"]]
    rem = store.all("SELECT ended_by FROM reminders WHERE at >= ?", (day_start(now),))
    return {"date": datetime.fromtimestamp(now, tz()).strftime("%Y-%m-%d"), "sessions": out, "total": total,
            "sts": sts_compare(store),
            "exercises": [{"id": r["id"], "kind": r["kind"], "at": r["started_at"], "reps": r["reps"],
                           "valid": bool(r["valid"]), "details": r["details"], "line": exercise_line(r)} for r in ex],
            "reminders": {"count": len(rem), "stood_up": sum(1 for x in rem if x["ended_by"] == "stood_up")}}


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
                    for r in training_rows(store, week) if not r["is_sample"]}
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


def therapist_summary(store: Store, now: float | None = None, days: int = 14) -> dict:
    """康复师一页：按天的日常活动与观测覆盖、每个动作的历史。只把同条件的记录放在一起，示例数据单独标出。"""
    now = now or time.time()
    since = day_start(now) - (days - 1) * 86400
    sessions = store.all("SELECT * FROM sessions WHERE started_at >= ? AND is_sample = 0 ORDER BY started_at", (since,))
    by_day: dict[str, dict] = {}
    for s_ in sessions:
        day = datetime.fromtimestamp(s_["started_at"], tz()).strftime("%m-%d")
        d = by_day.setdefault(day, {"day": day, "on_s": 0.0, "observable_s": 0.0, "rises_complete": 0,
                                    "rises_incomplete": 0, "rise_durations": [], "longest_sit_s": None,
                                    "trainings": 0, "reminders": 0})
        if s_["mode"] != "daily":
            continue
        st = session_stats(store, s_["id"], now)
        for k in ("on_s", "observable_s", "rises_complete", "rises_incomplete"):
            d[k] += st[k]
        d["rise_durations"] += [r["duration_s"] for r in store.all(
            "SELECT duration_s FROM rise_events WHERE session_id = ? AND complete = 1 AND duration_s IS NOT NULL",
            (s_["id"],))]
        if st["longest_sit_s"] and (d["longest_sit_s"] is None or st["longest_sit_s"] > d["longest_sit_s"]):
            d["longest_sit_s"] = st["longest_sit_s"]
    for r in store.all("SELECT at FROM reminders WHERE at >= ?", (since,)):
        day = datetime.fromtimestamp(r["at"], tz()).strftime("%m-%d")
        if day in by_day:
            by_day[day]["reminders"] += 1
    rows = training_rows(store, since)
    for r in rows:
        day = datetime.fromtimestamp(r["started_at"], tz()).strftime("%m-%d")
        if day in by_day and not r["is_sample"]:
            by_day[day]["trainings"] += 1
    daily = []
    for d in by_day.values():
        durs = d.pop("rise_durations")
        d["rise_median_s"] = round(statistics.median(durs), 2) if durs else None
        d["coverage"] = round(d["observable_s"] / d["on_s"], 2) if d["on_s"] else None
        daily.append({k: round(v, 1) if isinstance(v, float) else v for k, v in d.items()})
    # 训练动作历史（含示例数据，按条件分组）
    hist = training_rows(store)
    exercises: dict[str, list[dict]] = {}
    for r in hist:
        cond_key = json.dumps(r["conditions"], sort_keys=True, ensure_ascii=False)
        exercises.setdefault(r["kind"], []).append({
            "id": r["id"], "at": r["started_at"], "reps": r["reps"], "valid": bool(r["valid"]),
            "invalid_reasons": r["invalid_reasons"], "is_sample": bool(r["is_sample"]), "conditions": r["conditions"],
            "condition_key": cond_key, "details": r["details"], "line": exercise_line(r)})
    for k in exercises:
        exercises[k] = exercises[k][-12:]
    return {"range": [since, now], "daily": daily, "exercises": exercises, "plan": store.plan(),
            "notes": ["角度标「实验性」：只测靠近摄像头的那条腿，误差可能超过 10°。",
                      "平衡只记保持秒数；晃动幅度现有方法测不准，不提供。扶没扶由本人自报。",
                      "只把条件相同（同一椅子、扶手规则、目标）的记录放在一起比较；示例数据带「示例」标记。"],
            "disclaimer": DISCLAIMER}


# ---- 大模型改写措辞（默认关闭；只给数字，核对不过就用模板句） ----

_LLM_CACHE: dict[str, tuple[float, str | None]] = {}  # 改写结果（None 表示核对没通过，10 分钟内不再试）
_LLM_INFLIGHT: set[str] = set()
_BANNED = ("好转", "变好", "变差", "恶化", "风险", "建议", "应该", "可以独处", "恢复", "正常", "异常", "放心")


def _numbers(text: str) -> list[str]:
    import re

    return re.findall(r"\d+(?:\.\d+)?", text)


def llm_rewrite(lines: list[str], call=None, style: str = "family", wait: bool = False) -> dict | None:
    """把模板句改得更口语。call(prompt) -> str 由调用方提供（没配置就是 None，直接用模板）。
    核对：输出里的每个数字都必须在模板句里出现过；不能出现评价性词语；不超过 90 个字（康复师 160）。
    推理模型一次要 3–4 秒：默认在后台改写、先返回 None（页面先显示模板句），改好后下次请求就拿到；wait=True 时同步等。"""
    if call is None or not lines:
        return None
    src = "".join(lines)
    key = style + src
    hit = _LLM_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return {"text": hit[1], "source": "llm"} if hit[1] else None
    if key in _LLM_INFLIGHT:
        return None
    if not wait:
        import threading

        _LLM_INFLIGHT.add(key)

        def work():
            try:
                _llm_job(lines, call, style, key)
            finally:
                _LLM_INFLIGHT.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return None
    out = _llm_job(lines, call, style, key)
    return {"text": out, "source": "llm"} if out else None


def _llm_job(lines: list[str], call, style: str, key: str) -> str | None:
    src = "".join(lines)
    limit = 140 if style == "therapist" else 80
    prompt = (f"把下面这段观察记录{STYLE_PROMPT.get(style, STYLE_PROMPT['family'])}。要求：只能使用原文里出现的数字，"
              f"不增加任何事实，不评价好坏，不推测原因，不给建议，不超过 {limit} 个字。原文：" + src)
    out = ""
    for _ in range(2):  # 推理模型偶尔思考太长、正文为空：再试一次
        try:
            out = (call(prompt) or "").strip()
        except Exception:  # 网络、鉴权、超时：一律退回模板（稍后再试）
            return None
        if out:
            break
    if not out:
        _LLM_CACHE[key] = (time.time() - 540, None)  # 1 分钟后允许再试
        return None
    allowed = set(_numbers(src))
    if (not out or len(out) > (160 if style == "therapist" else 90) or any(n not in allowed for n in _numbers(out))
            or any(w in out for w in _BANNED)):
        _LLM_CACHE[key] = (time.time(), None)
        return None
    _LLM_CACHE[key] = (time.time(), out)
    return out


def llm_caller():
    """按环境变量配置 OpenAI 兼容接口：JW_LLM_URL（…/v1）、JW_LLM_KEY、JW_LLM_MODEL。没配置返回 None。"""
    import os
    import urllib.request

    url, key, model = os.environ.get("JW_LLM_URL"), os.environ.get("JW_LLM_KEY"), os.environ.get("JW_LLM_MODEL")
    if not (url and key and model):
        return None

    def call(prompt: str) -> str:
        # 推理模型（如 DeepSeek V4.1 Flash）先思考再回答：上限给足，不然思考用完额度、正文为空
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                           "temperature": 0.3, "max_tokens": 4000}).encode()
        req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())["choices"][0]["message"].get("content") or ""

    return call


# ---- 趋势（近两周）与 AI 总结：只陈述数字和同条件的前后对比，不说「好转 / 变差」 ----


def _day_key(t: float) -> str:
    return datetime.fromtimestamp(t, tz()).strftime("%m-%d")


def trends(store: Store, now: float | None = None, days: int = 14) -> dict:
    """按天汇总近 days 天的日常活动，以及各训练动作的历史。示例数据（is_sample）单独标出，不和真实数据混算对比。"""
    now = now or time.time()
    start = day_start(now) - (days - 1) * 86400
    keys = [_day_key(start + i * 86400 + 3600) for i in range(days)]
    rows = {k: {"day": k, "rises_complete": 0, "rises_incomplete": 0, "rise_durations": [], "observable_s": 0.0,
                "on_s": 0.0, "longest_sit_s": None, "reminders": 0, "trainings": 0, "sample": None, "observed": False}
            for k in keys}
    for s_ in store.all("SELECT * FROM sessions WHERE started_at >= ? ORDER BY started_at", (start,)):
        k = _day_key(s_["started_at"])
        if k not in rows or s_["mode"] != "daily":
            continue
        r = rows[k]
        st = session_stats(store, s_["id"], now)
        r["observed"] = True
        r["sample"] = bool(s_["is_sample"]) if r["sample"] is None else (r["sample"] and bool(s_["is_sample"]))
        for f in ("rises_complete", "rises_incomplete", "observable_s", "on_s"):
            r[f] += st[f]
        r["rise_durations"] += [x["duration_s"] for x in store.all(
            "SELECT duration_s FROM rise_events WHERE session_id = ? AND complete = 1 AND duration_s IS NOT NULL", (s_["id"],))]
        if st["longest_sit_s"] and (r["longest_sit_s"] is None or st["longest_sit_s"] > r["longest_sit_s"]):
            r["longest_sit_s"] = st["longest_sit_s"]
    for x in store.all("SELECT at FROM reminders WHERE at >= ?", (start,)):
        k = _day_key(x["at"])
        if k in rows:
            rows[k]["reminders"] += 1
    ex: dict[str, list[dict]] = {}
    for t in training_rows(store, start):
        k = _day_key(t["started_at"])
        if k in rows:
            rows[k]["trainings"] += 1
        d = t["details"]
        ex.setdefault(t["kind"], []).append({
            "day": k, "at": t["started_at"], "reps": t["reps"], "valid": bool(t["valid"]), "sample": bool(t["is_sample"]),
            "condition_key": json.dumps(t["conditions"], sort_keys=True, ensure_ascii=False),
            "hold_mean_s": d.get("hold_mean_s"), "best_s": d.get("best_s")})
    daily = []
    for k in keys:
        r = rows[k]
        durs = r.pop("rise_durations")
        r["rise_median_s"] = round(statistics.median(durs), 2) if durs else None
        r["observable_min"] = round(r.pop("observable_s") / 60)
        r["on_min"] = round(r.pop("on_s") / 60)
        r["longest_sit_min"] = None if r["longest_sit_s"] is None else round(r.pop("longest_sit_s") / 60)
        r.pop("longest_sit_s", None)
        r["sample"] = bool(r["sample"])
        daily.append(r)
    # 训练动作只保留和最近一次同条件的记录（不同椅子 / 扶手规则 / 目标不放在一起）
    for kind, recs in ex.items():
        key = recs[-1]["condition_key"]
        ex[kind] = [r for r in recs if r["condition_key"] == key and r["valid"]]
    return {"days": keys, "daily": daily, "exercises": ex,
            "has_sample": any(r["sample"] for r in daily) or any(r["sample"] for v in ex.values() for r in v),
            "facts": trend_facts(daily, ex)}


def _avg(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def trend_facts(daily: list[dict], ex: dict[str, list[dict]]) -> list[dict]:
    """几条可核对的趋势事实（每条带 audience：family / therapist / all）。前后 7 天各自取有观察的天来平均。"""
    out: list[dict] = []
    seen = [r for r in daily if r["observed"]]
    early = [r for r in seen if daily.index(r) < len(daily) - 7]
    late = [r for r in seen if daily.index(r) >= len(daily) - 7]
    trained_late = sum(1 for r in daily[-7:] if r["trainings"])
    if trained_late:
        out.append({"audience": "all", "text": f"最近 7 天里有 {trained_late} 天做了训练。"})
    a, b = _avg([r["rises_complete"] for r in late]), _avg([r["rises_complete"] for r in early])
    if a is not None:
        t = f"有观察的日子里，近 7 天平均每天完整看到站起来 {a:.0f} 次"
        out.append({"audience": "all", "text": t + (f"；再之前 7 天平均 {b:.0f} 次。" if b is not None else "。")})
    a, b = _avg([r["rise_median_s"] for r in late]), _avg([r["rise_median_s"] for r in early])
    if a is not None and b is not None:
        out.append({"audience": "therapist", "text": f"离座到站直的用时（每天中位数）：近 7 天平均 {a:.1f} 秒，再之前 7 天 {b:.1f} 秒。"})
    a, b = _avg([r["longest_sit_min"] for r in late]), _avg([r["longest_sit_min"] for r in early])
    if a is not None and b is not None:
        out.append({"audience": "therapist", "text": f"每天最长连续坐着：近 7 天平均 {a:.0f} 分钟，再之前 7 天 {b:.0f} 分钟（只算看得到的部分）。"})
    sts = ex.get("sts30") or []
    if len(sts) >= 2:
        out.append({"audience": "all", "text": f"30 秒坐站（同样条件）：最近一次 {sts[-1]['reps']} 次，这段时间第一次 {sts[0]['reps']} 次。"})
    elif sts:
        out.append({"audience": "all", "text": f"30 秒坐站：最近一次 {sts[-1]['reps']} 次。"})
    ke = ex.get("knee_ext") or []
    if ke and ke[-1].get("hold_mean_s") is not None:
        t = f"坐位伸膝：最近一次 {ke[-1]['reps']} 次，平均每次保持 {ke[-1]['hold_mean_s']:.1f} 秒"
        if len(ke) >= 2 and ke[0].get("hold_mean_s") is not None:
            t += f"；这段时间第一次平均保持 {ke[0]['hold_mean_s']:.1f} 秒"
        out.append({"audience": "therapist", "text": t + "。"})
    bal = ex.get("balance") or []
    if bal and bal[-1].get("best_s") is not None:
        t = f"站立平衡：最近一次最长保持 {bal[-1]['best_s']:.1f} 秒"
        if len(bal) >= 2 and bal[0].get("best_s") is not None:
            t += f"，这段时间第一次 {bal[0]['best_s']:.1f} 秒"
        out.append({"audience": "therapist", "text": t + "（扶没扶由本人自报）。"})
    rem = [r["reminders"] for r in daily[-7:] if r["observed"]]
    if rem and sum(rem):
        out.append({"audience": "therapist", "text": f"近 7 天久坐提醒共 {sum(rem)} 次。"})
    return out


STYLE_PROMPT = {
    "family": "改写成给子女看的一两句话，语气温和、口语化",
    "therapist": "改写成给康复师看的简明要点，客观、专业，可以分成 2–4 句",
    "elder": "改写成给老人本人看的一两句话，用大白话、语气亲切",
}


def ai_summary(facts: list[dict], audience: str, call=None, has_sample: bool = False, wait: bool = False) -> dict:
    """AI 总结：先用模板把事实串起来；配置了大模型就改写得更自然，但数字必须一一对得上，否则退回模板。
    含示例数据时不往正文里加注，返回 sample=True，由页面显示「示例」标签。"""
    lines = [f["text"] for f in facts if f["audience"] in ("all", audience)]
    if audience in ("family", "elder"):
        lines = lines[:3]
    if not lines:
        return {"text": "这段时间的记录还不够，暂时没有总结。", "source": "template", "facts": [], "sample": False}
    template = "".join(lines)
    rw = llm_rewrite(lines, call, style=audience, wait=wait)
    return {"text": rw["text"] if rw else template, "source": "llm" if rw else "template", "facts": lines, "sample": has_sample}

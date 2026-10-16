"""SQLite 存储：只存事件与数字，没有任何图片、视频或关键点字段。时间用 epoch 秒。"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, mode TEXT, label TEXT, status TEXT,
  started_at REAL, ended_at REAL, is_sample INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS intervals(
  id INTEGER PRIMARY KEY, session_id INTEGER, observable INTEGER, reason TEXT, t_start REAL, t_end REAL);
CREATE TABLE IF NOT EXISTS rise_events(
  id INTEGER PRIMARY KEY, session_id INTEGER, complete INTEGER, reason TEXT,
  t_start REAL, t_end REAL, duration_s REAL, other_person INTEGER, assist TEXT);
CREATE TABLE IF NOT EXISTS sit_bouts(
  id INTEGER PRIMARY KEY, session_id INTEGER, t_start REAL, t_end REAL, truncated INTEGER);
CREATE TABLE IF NOT EXISTS trainings(
  id INTEGER PRIMARY KEY, session_id INTEGER, kind TEXT, started_at REAL, reps INTEGER, valid INTEGER,
  invalid_reasons TEXT, conditions TEXT, note TEXT, is_sample INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS share(
  id INTEGER PRIMARY KEY CHECK (id = 1), level TEXT, family_token TEXT, updated_at REAL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS access_log(id INTEGER PRIMARY KEY, who TEXT, path TEXT, at REAL, allowed INTEGER);
CREATE TABLE IF NOT EXISTS device_log(id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, at REAL, latency_ms REAL);
"""

# 训练条件：只有条件完全相同的记录才能放在一起比较
DEFAULT_CONDITIONS = {"preset": 2, "chair": "训练椅 A", "armrest": "不用扶手", "assist": "旁边有人看护"}


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            if not self.db.execute("SELECT 1 FROM share WHERE id = 1").fetchone():
                self.db.execute("INSERT INTO share(id, level, family_token, updated_at) VALUES (1, 'none', ?, ?)",
                                (secrets.token_urlsafe(16), time.time()))
            self.db.commit()

    # ---- 通用 ----

    def exec(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    # ---- 业务 ----

    def add_session(self, mode: str, label: str, at: float) -> int:
        return self.exec("INSERT INTO sessions(mode, label, status, started_at) VALUES (?, ?, 'starting', ?)",
                         (mode, label, at))

    def set_session(self, sid: int, **kw) -> None:
        cols = ", ".join(f"{k} = ?" for k in kw)
        self.exec(f"UPDATE sessions SET {cols} WHERE id = ?", (*kw.values(), sid))

    def add_interval(self, sid: int, observable: bool, reason: str | None, t0: float, t1: float) -> None:
        if t1 - t0 >= 0.05:
            self.exec("INSERT INTO intervals(session_id, observable, reason, t_start, t_end) VALUES (?, ?, ?, ?, ?)",
                      (sid, int(observable), reason, t0, t1))

    def add_rise(self, sid: int, e: dict, wall) -> int:
        return self.exec(
            "INSERT INTO rise_events(session_id, complete, reason, t_start, t_end, duration_s, other_person) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, int(e["complete"]), e["reason"], wall(e["t_start"]), wall(e["t_end"]), e["duration_s"],
             int(e["other_person"])))

    def add_sit_bout(self, sid: int, e: dict, wall) -> None:
        self.exec("INSERT INTO sit_bouts(session_id, t_start, t_end, truncated) VALUES (?, ?, ?, ?)",
                  (sid, wall(e["t_start"]), wall(e["t_end"]), int(e["truncated"])))

    def add_training(self, sid: int | None, kind: str, at: float, res: dict, conditions: dict,
                     is_sample: bool = False) -> int:
        return self.exec(
            "INSERT INTO trainings(session_id, kind, started_at, reps, valid, invalid_reasons, conditions, is_sample) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, kind, at, res["reps"], int(res["valid"]), json.dumps(res.get("invalid_reasons", [])),
             json.dumps(conditions, ensure_ascii=False), int(is_sample)))

    def log_device(self, kind: str, payload: dict, latency_ms: float | None = None) -> None:
        self.exec("INSERT INTO device_log(kind, payload, at, latency_ms) VALUES (?, ?, ?, ?)",
                  (kind, json.dumps(payload, ensure_ascii=False), time.time(), latency_ms))

    def share(self) -> dict:
        return self.one("SELECT * FROM share WHERE id = 1")

    def set_share(self, level: str) -> dict:
        now = time.time()
        if level == "none":
            self.exec("UPDATE share SET level = 'none', revoked_at = ?, updated_at = ? WHERE id = 1", (now, now))
        else:
            self.exec("UPDATE share SET level = ?, revoked_at = NULL, updated_at = ? WHERE id = 1", (level, now))
        return self.share()

    def log_access(self, who: str, path: str, allowed: bool) -> None:
        self.exec("INSERT INTO access_log(who, path, at, allowed) VALUES (?, ?, ?, ?)", (who, path, time.time(), int(allowed)))

    def delete(self, since: float | None) -> None:
        """删除 since 之后的全部记录；since 为 None 时全部删除。示例数据保留。"""
        since = since or 0.0
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM sessions WHERE started_at >= ?", (since,)).fetchall()]
            for t in ("intervals", "rise_events", "sit_bouts"):
                self.db.executemany(f"DELETE FROM {t} WHERE session_id = ?", [(i,) for i in ids])
            self.db.execute("DELETE FROM trainings WHERE started_at >= ? AND is_sample = 0", (since,))
            self.db.execute("DELETE FROM sessions WHERE started_at >= ?", (since,))
            self.db.commit()

    def purge_older_than(self, days: int) -> None:
        cutoff = time.time() - days * 86400
        self.delete_before(cutoff)

    def delete_before(self, cutoff: float) -> None:
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM sessions WHERE started_at < ?", (cutoff,)).fetchall()]
            for t in ("intervals", "rise_events", "sit_bouts"):
                self.db.executemany(f"DELETE FROM {t} WHERE session_id = ?", [(i,) for i in ids])
            self.db.execute("DELETE FROM sessions WHERE started_at < ?", (cutoff,))
            self.db.execute("DELETE FROM trainings WHERE started_at < ? AND is_sample = 0", (cutoff,))
            self.db.execute("DELETE FROM device_log WHERE at < ?", (cutoff,))
            self.db.execute("DELETE FROM access_log WHERE at < ?", (cutoff,))
            self.db.commit()

    def close_stale_sessions(self) -> None:
        """服务端重启后，内存里的会话已丢失：把数据库里没收尾的会话标为已结束（结束时间取最后一个观测区间）。"""
        self.exec("UPDATE sessions SET status = 'ended', ended_at = COALESCE("
                  "(SELECT MAX(t_end) FROM intervals WHERE session_id = sessions.id), started_at) "
                  "WHERE status != 'ended'")

    def seed_samples(self) -> None:
        """示例历史（前两周的 30 秒坐站），界面上一律带「示例」水印。只在没有示例时写入。"""
        if self.one("SELECT 1 FROM trainings WHERE is_sample = 1"):
            return
        now = time.time()
        for days_ago, reps in ((14, 6), (7, 7)):
            self.add_training(None, "sts30", now - days_ago * 86400, {"reps": reps, "valid": True},
                              DEFAULT_CONDITIONS, is_sample=True)

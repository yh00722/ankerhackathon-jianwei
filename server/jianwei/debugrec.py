"""调试记录：逐帧记下数字特征，用于离线回放调参（scripts/replay.py）。

- 只有数字：f1、髋角、膝角、可见度、分类结果、不可观测原因、画面位移量；没有画面，也没有关键点坐标。
- 默认关闭，只在团队自己测试时打开（控制台「调试记录」）；演示前用 DELETE /api/debug/records 全部删除。
- 文件：server/data/debug/年月日-时分秒.jsonl，一行一帧；事件行带 "ev" 字段。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class DebugRecorder:
    def __init__(self, dirpath: str | Path):
        self.dir = Path(dirpath)
        self._f = None
        self.name: str | None = None
        self.lines = 0
        self._flush_at = 0.0

    @property
    def on(self) -> bool:
        return self._f is not None

    def start(self) -> str:
        self.stop()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = time.strftime("%Y%m%d-%H%M%S") + ".jsonl"
        self._f = open(self.dir / self.name, "a", encoding="utf-8")
        self.lines = 0
        return self.name

    def stop(self) -> None:
        if self._f:
            self._f.close()
        self._f = None

    def write(self, d: dict) -> None:
        if not self._f:
            return
        self._f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.lines += 1
        now = time.monotonic()
        if now - self._flush_at > 1.0:
            self._f.flush()
            self._flush_at = now

    def files(self) -> list[dict]:
        if not self.dir.exists():
            return []
        return [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(self.dir.glob("*.jsonl"))]

    def delete_all(self) -> int:
        self.stop()
        n = 0
        for f in self.dir.glob("*.jsonl") if self.dir.exists() else []:
            f.unlink()
            n += 1
        return n

    def status(self) -> dict:
        return {"recording": self.on, "file": self.name if self.on else None, "lines": self.lines,
                "files": len(self.files())}

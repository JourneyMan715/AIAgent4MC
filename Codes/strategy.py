# -*- coding: utf-8 -*-
"""
策略（Strategy）—— 比赛前/比赛中可改的「打法」配置文件。

为什么要单独一层：
- 提示词、技能、兜底策略都是代码，赛前想换打法不该去改 Python；
- 把「这局怎么打」抽成 `Codes/data/strategy.json`，改完**下一个决策就生效**（不用重启）；
- 测试不同策略时，只换这个文件，跑同样时长，用 `python main.py report` 对比。

字段（都可以不写，缺省用 DEFAULT）：
    mode            打法名（给自己看的，如 balanced / nether_rush / safe_bootstrap）
    risk            low | medium | high：影响提示词里的冒险程度
    branch_priority 优先分支（nether / village / trial / husbandry / adventure / end）
    phases          时间规划：[{name, until_min, focus}]
    targets         明确要冲的 advancement id
    avoid           明确不碰的 advancement id
    notes           给自己看的备注
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "data", "strategy.json")
DOC_PATH = os.path.join(_HERE, "data", "strategy.md")

DEFAULT = {
    "mode": "custom",
    "risk": "medium",
    "branch_priority": [],
    "phases": [],
    "targets": [],
    "avoid": [],
    "notes": "",
}

VALID_RISK = {"low", "medium", "high"}
VALID_FOCUS = {"bootstrap", "nether", "end", "best_available", "cheap_points", "custom"}


def _merge(base, loaded):
    data = dict(base)
    for key, value in (loaded or {}).items():
        if key in ("mode", "risk", "notes") and isinstance(value, str):
            data[key] = value
        elif key in ("branch_priority", "targets", "avoid") and isinstance(value, list):
            data[key] = [str(v) for v in value if str(v).strip()]
        elif key == "phases" and isinstance(value, list):
            phases = []
            for p in value:
                if not isinstance(p, dict):
                    continue
                try:
                    until = int(p.get("until_min", 0))
                except (TypeError, ValueError):
                    continue
                focus = str(p.get("focus") or "best_available")
                if focus not in VALID_FOCUS:
                    focus = "best_available"
                phases.append({
                    "name": str(p.get("name") or f"阶段{len(phases) + 1}"),
                    "until_min": max(1, until),
                    "focus": focus,
                })
            if phases:
                data["phases"] = sorted(phases, key=lambda x: x["until_min"])
    if data.get("risk") not in VALID_RISK:
        data["risk"] = "medium"
    return data


class Strategy:
    """读策略文件；按 mtime 自动热更新（比赛里改完下一轮生效）。

    两种来源：
    - `data/strategy.md`（推荐）：用户自由写，LLM 自己阅读/拆解，不预设阶段；
    - `data/strategy.json`（兼容）：结构化的 phases/targets/avoid。
    两者同时存在时，strategy.md 优先，LLM 以自由文本为准。
    """

    def __init__(self, path=None, doc_path=None):
        self.path = path or DEFAULT_PATH
        if not os.path.isabs(self.path):
            self.path = os.path.join(_HERE, self.path)
        self.doc_path = doc_path or DOC_PATH
        if not os.path.isabs(self.doc_path):
            self.doc_path = os.path.join(_HERE, self.doc_path)
        self._mtime = None
        self._doc_mtime = None
        self._doc_text = ""
        self._data = dict(DEFAULT)

    def load_doc(self, force=False):
        try:
            mtime = os.path.getmtime(self.doc_path)
        except OSError:
            self._doc_text = ""
            self._doc_mtime = None
            return ""
        if force or self._doc_mtime != mtime:
            try:
                with open(self.doc_path, "r", encoding="utf-8") as fh:
                    self._doc_text = fh.read(8000)
                self._doc_mtime = mtime
            except OSError:
                pass
        return self._doc_text

    def doc_text(self):
        self.load_doc()
        return (self._doc_text or "").strip()

    def has_doc(self):
        return bool(self.doc_text())

    def load(self, force=False):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self._data = dict(DEFAULT)
            self._mtime = None
            return self._data
        if force or self._mtime != mtime:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data = _merge(DEFAULT, loaded if isinstance(loaded, dict) else {})
                self._mtime = mtime
            except (OSError, ValueError):
                # 文件改坏了就沿用上一份，别让比赛中断
                pass
        return self._data

    def data(self):
        return dict(self._data)

    # ---------------- 查询 ----------------
    def phase(self, elapsed_min):
        elapsed = max(0.0, float(elapsed_min or 0))
        phases = self._data.get("phases") or []
        if not phases:
            return {"name": "未设阶段", "until_min": 0, "focus": "custom"}
        for phase in phases:
            if elapsed < float(phase["until_min"]):
                return dict(phase)
        return dict(phases[-1])

    def avoid_ids(self):
        return set(self._data.get("avoid") or [])

    def target_ids(self):
        return list(self._data.get("targets") or [])

    def summary(self, elapsed_min=None):
        """给大模型看的一行策略摘要（尽量短）。"""
        phase = self.phase(elapsed_min) if elapsed_min is not None else self.phase(0)
        parts = [
            f"mode={self._data.get('mode')}",
            f"risk={self._data.get('risk')}",
            f"phase={phase.get('name')}(focus={phase.get('focus')})",
        ]
        targets = self.target_ids()
        if targets:
            parts.append("targets=" + ",".join(targets[:3]))
        avoid = self.avoid_ids()
        if avoid:
            parts.append(f"avoid={len(avoid)}项")
        return "；".join(parts)

    def brief(self, elapsed_min):
        """给提示词用的策略：用户自由文本优先；否则退回结构化 JSON。"""
        text = self.doc_text()
        if text:
            return {
                "source": "strategy.md",
                "text": text[:6000],
            }
        phase = self.phase(elapsed_min)
        return {
            "mode": self._data.get("mode"),
            "risk": self._data.get("risk"),
            "phase": phase.get("name"),
            "focus": phase.get("focus"),
            "branch_priority": (self._data.get("branch_priority") or [])[:4],
            "targets": self.target_ids()[:4],
            "avoid": sorted(self.avoid_ids())[:6],
        }

    def summary_text(self, elapsed_min=0):
        text = self.doc_text()
        if text:
            return f"strategy.md：{len(text)} 字（LLM 自行拆解，不预设阶段）"
        return self.summary(elapsed_min)

# -*- coding: utf-8 -*-
"""
世界状态 + 记忆 —— 程序重启不丢东西。

存的东西分三类：
1. **当前状态**：坐标、血量、饥饿、背包、手上的东西、维度；
2. **世界知识**：进度（advancement）、路点（家/工作台/矿口）、失败记录、黑名单；
3. **运行账本**：token 用量、决策轮数、各技能调用次数、死亡时间。

一句话：**关机重开，Agent 还记得自己是谁、干到哪了。**
这也是比赛规则里说的「状态维护」「记忆」。
"""

import json
import os
import time


def _now():
    return time.time()


DEFAULT_STATE = {
    "version": 2,
    "run_id": "",
    "started_at": 0.0,
    "updated_at": 0.0,

    # ---- 当前状态 ----
    "pos": None,
    "health": 20.0,
    "food": 20.0,
    "dimension": "",
    "held": None,
    "xp_level": 0,
    "is_day": True,
    "biome": "",
    "hostile": None,
    "inventory": {},
    "inventory_at": 0.0,

    # ---- 世界知识 ----
    "advancements": {},     # id -> {title, frame, at, score}
    "pending": [],          # [{id, title, frame, score}]（按分值从高到低，已剔除 0 分项）
    "island": None,         # 虚空岛勘察结果：{"features": {...}, "top": {...}}（RULES.md v2 用）
    "target": None,         # {"kind": ..., "id": ..., "why": ...}
    "waypoints": {},        # name -> [x, y, z]
    "failures": {},         # key -> {count, last}
    "blacklist": {},        # key -> 到期时间戳
    "recent": [],           # [{t, skill, ok, msg}]
    "events": [],           # 最近的事件摘要（chat/hurt/death/pickup…）

    # ---- 运行账本 ----
    "tokens": {"input": 0, "output": 0, "calls": 0},
    "stats": {"ticks": 0, "actions": 0, "skills": {}, "deaths": []},
    "last_death": None,
}

MAX_RECENT = 8
MAX_EVENTS = 12


class WorldState:
    def __init__(self, path):
        self.path = path
        self.data = json.loads(json.dumps(DEFAULT_STATE))
        self._loaded = False

    # ================= 读 / 写 =================
    def load(self):
        """从磁盘恢复；文件不存在或损坏就从空状态开始。"""
        if not os.path.isfile(self.path):
            self.data["run_id"] = time.strftime("%Y%m%d-%H%M%S")
            self.data["started_at"] = _now()
            self._loaded = True
            return self
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                base = json.loads(json.dumps(DEFAULT_STATE))
                base.update(loaded)
                self.data = base
                self._loaded = True
        except (OSError, ValueError):
            print("[状态] 读取失败，按新开局处理")
            self.data["run_id"] = time.strftime("%Y%m%d-%H%M%S")
            self.data["started_at"] = _now()
        return self

    def save(self):
        """原子写入：先写临时文件再替换，避免断电写坏。"""
        self.data["updated_at"] = _now()
        try:
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            print("[状态] 保存失败：", e)

    def reset(self):
        """新比赛开始：清空一切，重新计时。"""
        self.data = json.loads(json.dumps(DEFAULT_STATE))
        self.data["run_id"] = time.strftime("%Y%m%d-%H%M%S")
        self.data["started_at"] = _now()
        self.save()

    # ================= 当前状态 =================
    def update_status(self, s):
        if not isinstance(s, dict):
            return
        pos = s.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 3:
            self.data["pos"] = [round(float(v), 2) for v in pos]
        for src, dst in (
            ("health", "health"), ("food", "food"), ("dimension", "dimension"),
            ("xp_level", "xp_level"), ("is_day", "is_day"), ("biome", "biome"),
        ):
            if src in s and s[src] is not None:
                self.data[dst] = s[src]
        held = s.get("held")
        self.data["held"] = held.get("name") if isinstance(held, dict) else None
        hostile = s.get("nearest_hostile")
        self.data["hostile"] = hostile if isinstance(hostile, dict) else None

    def update_inventory(self, inv_result):
        """把 /inventory 的返回压成 {名字: 数量}。"""
        counts = {}
        if isinstance(inv_result, dict):
            for item in inv_result.get("items") or []:
                if isinstance(item, dict) and item.get("name"):
                    counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
            held = inv_result.get("held")
            if isinstance(held, dict) and held.get("name"):
                counts.setdefault(held["name"], 0)
        self.data["inventory"] = counts
        self.data["inventory_at"] = _now()

    def inventory(self):
        return dict(self.data.get("inventory") or {})

    def has(self, item, count=1):
        return int(self.inventory().get(item, 0)) >= count

    def inventory_age(self):
        return _now() - float(self.data.get("inventory_at") or 0)

    # ================= 进度 =================
    def update_advancements(self, adv_result, scorer):
        """合并 /advancements 的结果，返回「这次新完成的」列表。

        scorer(id, frame) -> 分数
        """
        new_items = []
        completed = {}
        pending = []
        if isinstance(adv_result, dict):
            for a in adv_result.get("advancements") or []:
                if not isinstance(a, dict) or not a.get("id"):
                    continue
                aid = a["id"]
                title = a.get("title") or aid
                frame = (a.get("frame") or "task").lower()
                if (a.get("status") or "").lower() == "completed":
                    completed[aid] = {
                        "title": title,
                        "frame": frame,
                        "at": a.get("completed_at") or _now(),
                        "score": int(scorer(aid, frame)),
                    }
                else:
                    pending.append({"id": aid, "title": title, "frame": frame,
                                    "score": int(scorer(aid, frame))})

        known = self.data.setdefault("advancements", {})
        for aid, info in completed.items():
            if aid not in known:
                known[aid] = info
                new_items.append(dict(info, id=aid))

        pending.sort(key=lambda x: x["score"], reverse=True)
        # 0 分（规则里标「不计分」）的不值得花时间，直接不进候选
        self.data["pending"] = [p for p in pending if p["score"] > 0][:40]
        return new_items

    def completed_ids(self):
        return set((self.data.get("advancements") or {}).keys())

    def score_total(self):
        return sum(int(v.get("score") or 0) for v in (self.data.get("advancements") or {}).values())

    # ================= 记忆 =================
    def add_recent(self, skill, ok, message):
        self.data.setdefault("recent", []).append({
            "t": round(_now(), 1),
            "skill": skill,
            "ok": bool(ok),
            "msg": str(message)[:160],
        })
        self.data["recent"] = self.data["recent"][-MAX_RECENT:]

    def add_event(self, text):
        self.data.setdefault("events", []).append({"t": round(_now(), 1), "text": str(text)[:160]})
        self.data["events"] = self.data["events"][-MAX_EVENTS:]

    def record_failure(self, key, blacklist_after=0, blacklist_seconds=0):
        failures = self.data.setdefault("failures", {})
        item = failures.setdefault(key, {"count": 0, "last": 0})
        item["count"] = int(item.get("count") or 0) + 1
        item["last"] = _now()
        if blacklist_after and blacklist_seconds and item["count"] >= int(blacklist_after):
            self.data.setdefault("blacklist", {})[key] = _now() + float(blacklist_seconds)
        return item["count"]

    def clear_failure(self, key):
        self.data.get("failures", {}).pop(key, None)

    def clear_failures_for(self, skill):
        """某个技能成功后，把它所有参数的失败/拉黑记录都清掉。"""
        prefix = f"{skill}:"
        for key in list(self.data.get("failures", {}).keys()):
            if key.startswith(prefix):
                self.data["failures"].pop(key, None)
        for key in list(self.data.get("blacklist", {}).keys()):
            if key.startswith(prefix):
                self.data["blacklist"].pop(key, None)

    def blocked_lines(self, limit=4):
        """当前还在拉黑期内的动作（给规划层看，别让它反复选）。"""
        now = _now()
        active = [(k, until) for k, until in (self.data.get("blacklist") or {}).items() if until > now]
        active.sort(key=lambda kv: -kv[1])
        return [k[:90] for k, _ in active[:limit]]

    def is_blacklisted(self, key):
        until = (self.data.get("blacklist") or {}).get(key)
        return bool(until and until > _now())

    def blacklist_until(self, key):
        return float((self.data.get("blacklist") or {}).get(key) or 0)

    def failure_lines(self, limit=4):
        lines = []
        for key, item in sorted((self.data.get("failures") or {}).items(),
                                key=lambda kv: kv[1].get("last", 0), reverse=True)[:limit]:
            lines.append(f"{key} ×{item.get('count', 1)}")
        return lines

    def add_waypoint(self, name, pos):
        self.data.setdefault("waypoints", {})[name] = [int(v) for v in pos]

    # ================= 账本 =================
    def update_tokens(self, usage):
        if isinstance(usage, dict):
            self.data["tokens"] = {
                "input": int(usage.get("input") or 0),
                "output": int(usage.get("output") or 0),
                "calls": int(usage.get("calls") or 0),
            }

    def bump(self, skill):
        stats = self.data.setdefault("stats", {})
        stats["actions"] = int(stats.get("actions") or 0) + 1
        skills = stats.setdefault("skills", {})
        skills[skill] = int(skills.get(skill) or 0) + 1

    def note_death(self):
        # 同一次死亡可能被 status 和 event 各记一次，3 秒内去重
        last = float(self.data.get("last_death") or 0)
        if _now() - last < 3:
            return
        self.data["last_death"] = _now()
        stats = self.data.setdefault("stats", {})
        deaths = stats.setdefault("deaths", [])
        deaths.append(_now())
        stats["deaths"] = deaths[-10:]

    def recent_deaths(self, window_s=60):
        t0 = _now() - window_s
        return [t for t in (self.data.get("stats", {}).get("deaths") or []) if t >= t0]

    # ================= 给大脑看的摘要 =================
    def brief(self, max_items=10):
        inv = self.inventory()
        top = sorted(inv.items(), key=lambda kv: kv[1], reverse=True)[:max_items]
        pending = (self.data.get("pending") or [])[:8]
        return {
            "pos": self.data.get("pos"),
            "hp": self.data.get("health"),
            "food": self.data.get("food"),
            "dim": self.data.get("dimension"),
            "xp": self.data.get("xp_level"),
            "held": self.data.get("held"),
            "inv": {k: v for k, v in top},
            "target": self.data.get("target"),
            "pending": [{"id": p["id"], "score": p["score"], "title": p.get("title")} for p in pending],
            "recent": [f"{r['skill']}:{r['msg']}" for r in (self.data.get("recent") or [])[-4:]],
            "fails": self.failure_lines(),
            "blocked": self.blocked_lines(),
            "adv_done": len(self.data.get("advancements") or {}),
            "score": self.score_total(),
            "island": self.data.get("island"),
        }

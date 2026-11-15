# -*- coding: utf-8 -*-
"""
规划层（Planner）—— 「这一步做什么」由大模型决定，但只做**一件事**。

和旧版最大的不同：
- 旧版每个动作问一次，还带整段聊天历史（token 爆炸）；
- 现在每次只发：固定系统提示（技能表）+ 一小段结构化状态（几百字），
  一次决策 = 一个技能调用 = 几十个底层动作。

这样 8 小时里能问几千次，而不是一百多次。
"""

import json

import config
from knowledge import KnowledgeBase
from skills import FOODS, SKILLS, skill_help
from strategy import Strategy


SYSTEM_PROMPT = """你是 Minecraft 生存比赛里一个 Bot 的调度器（不是聊天助手）。
比赛 8 小时，排名只看「完成的 advancement 得分」：每项分值**只由难度决定**（1/2/3/4/5 → 5/15/30/60/100）；
规则里标「不计分」的条目不会出现在候选里。你每次只做一个决定：从下面技能里选**一个**并给参数，程序会自己跑完（可能几十秒）。

场地：你在一座**自己的虚空岛**上（四周是虚空），岛上有/没有什么结构，决定了能冲哪些进度。
不要走太远、不要掉下去；不确定岛上有什么就先 survey。

可用技能：
{skills}

决策原则（按优先级）：
1. 保命第一：饥饿低先吃，血量低有怪先跑（程序也会兜底）。
2. 先补基础：没木头就 chop_tree；没工作台就 craft；没镐就 craft；再挖石头/煤/铁。
3. 有基础后，优先冲 pending 里「分值高、岛上条件允许」的 advancement（target 填它的 id）。
4. 最近失败过的技能不要原样重试：换参数、换目标，或先 scan 看看。
5. 一个决定只做一件事，不要写计划，不要长篇解释。
6. 不确定环境时用 scan；开局/换岛后用 survey 勘察结构。
7. 只做 island.features 里有的路线（比如没有村庄就别想着交易）。
8. **blocked 里的动作被拉黑了，绝对不要选**；缺材料时先合成中间产物（木板、木棍），不要反复重试同一个动作。
9. 用户会在 `strategy` 里写这局怎么打（自由文本）。**你必须自己阅读、拆解成当前这一步**，不要假设固定阶段/固定路线；
   strategy 为空时，优先保证生存和基础盘，不要擅自冲分。
10. `memory` 是学习层积累的游戏规则/技能经验；优先使用其中已验证的结论，不确定时先 scan/survey 验证。

只输出一个 JSON 对象，不要代码块、不要多余文字：
{{"skill":"技能名","args":{{...}},"target":"要冲的 advancement id 或空字符串","why":"一句话"}}
"""


def _brief_text(state, time_left_min, tokens_left, pending, strategy_brief=None,
                memory_text=None):
    """把世界状态压成给模型看的一小段 JSON（越短越省 token）。"""
    data = state.brief(max_items=10)
    data["time_left_min"] = round(time_left_min) if time_left_min is not None else None
    if tokens_left is not None:
        data["tokens_left"] = int(tokens_left)
    data["strategy"] = strategy_brief or {}
    if memory_text:
        data["memory"] = memory_text
    data["pending"] = [
        {"id": p.get("id"), "score": p.get("score"), "title": p.get("title")}
        for p in (pending or [])[:6]
    ]
    data["target"] = (state.data.get("target") or {}).get("id")
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _key(skill, args):
    try:
        payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(args)
    return f"{skill}:{payload}"[:140]


class Planner:
    def __init__(self, llm, *, max_candidates=8, strategy=None, strategy_path=None):
        self.llm = llm
        self.max_candidates = max_candidates
        self.system = SYSTEM_PROMPT.format(skills=skill_help())
        self.strategy = strategy or Strategy(
            strategy_path, doc_path=getattr(config, "STRATEGY_DOC", None))
        self.knowledge = KnowledgeBase()

    # ---------------- 对外 ----------------
    def decide(self, state, *, time_left_min=None, tokens_left=None, elapsed_min=None):
        """返回 {'skill','args','target','why'}；模型失败时退回本地策略。"""
        self.strategy.load()                     # strategy.json 兼容
        self.strategy.load_doc()                 # strategy.md 优先
        self.knowledge.load()
        pending = state.data.get("pending") or []
        avoid = self.strategy.avoid_ids()
        pending = [p for p in pending if p.get("id") not in avoid]
        targets = set(self.strategy.target_ids())
        if targets:
            pending.sort(key=lambda p: (p.get("id") not in targets, -int(p.get("score") or 0)))

        user = _brief_text(state, time_left_min, tokens_left, pending,
                           self.strategy.brief(elapsed_min or 0),
                           self.knowledge.summary())
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user},
        ]
        obj = self.llm.chat_json(messages)
        decision = self._validate(obj)
        if decision is None:
            decision = self._fallback(state, reason="模型给的技能不合法")
        return decision

    # ---------------- 内部 ----------------
    def _validate(self, obj):
        if not isinstance(obj, dict):
            return None
        skill = obj.get("skill") or obj.get("action")
        if not isinstance(skill, str):
            return None
        skill = skill.strip().lower()

        if skill in ("stop", "done", "finish", "__finish__"):
            return {"skill": "__finish__", "args": {}, "target": "", "why": obj.get("why", "")}

        if skill not in SKILLS:
            return None

        args = obj.get("args")
        if not isinstance(args, dict):
            args = {}
        target = obj.get("target")
        if not isinstance(target, str):
            target = ""
        if target and target in self.strategy.avoid_ids():
            target = ""
        why = str(obj.get("why") or "")[:120]
        return {"skill": skill, "args": args, "target": target, "why": why}

    def _fallback(self, state, reason=""):
        """模型不靠谱时的兜底策略：保证还能推进游戏进度，并且避开被拉黑的动作。"""
        inv = state.inventory()
        food = float(state.data.get("food") or 20)
        has_food = any(int(inv.get(name, 0)) > 0 for name in FOODS)
        has_pickaxe = any(name.endswith("_pickaxe") for name in inv)
        logs = sum(int(v) for k, v in inv.items()
                   if k.endswith("_log") or k.endswith("_wood"))
        planks = sum(int(v) for k, v in inv.items() if k.endswith("_planks"))
        cobble = int(inv.get("cobblestone", 0))

        candidates = []
        if food < 14 and has_food:
            candidates.append(("eat", {}))
        if not state.data.get("island"):
            candidates.append(("survey", {}))
        if logs < 3 and planks < 6:
            candidates.append(("chop_tree", {}))
        if not has_pickaxe:
            candidates.append(("craft", {"item": "wooden_pickaxe", "count": 1}))
        if cobble < 20:
            candidates.append(("collect", {"group": "stone", "count": 12}))
        candidates += [("scan", {"radius": 16}), ("explore", {"distance": 12})]

        for skill, args in candidates:
            if not state.is_blacklisted(_key(skill, args)):
                return {"skill": skill, "args": args, "target": "",
                        "why": reason or f"兜底：{skill}"}

        return {"skill": "survey", "args": {}, "target": "",
                "why": reason or "兜底勘察"}

# -*- coding: utf-8 -*-
"""通用死循环检测：连续 N 轮相同签名，就判定卡死。

签名通常包含「动作 + 参数 + 世界状态」；只比动作会误杀“重复采集直到背包变化”的正常循环。
"""

import json
from collections import deque


class LoopGuard:
    def __init__(self, limit=10):
        self.limit = max(2, int(limit or 10))
        self._items = deque(maxlen=self.limit)
        self.trips = 0

    def reset(self):
        self._items.clear()

    def hit(self, signature):
        """记录一次签名；连续 limit 次完全相同则返回 True。"""
        text = self._normalize(signature)
        self._items.append(text)
        if len(self._items) < self.limit:
            return False
        if len(set(self._items)) == 1:
            self.trips += 1
            return True
        return False

    @staticmethod
    def _normalize(signature):
        if isinstance(signature, str):
            return signature[:500]
        try:
            return json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str)[:500]
        except (TypeError, ValueError):
            return str(signature)[:500]


def state_signature(state):
    """世界状态摘要：位置/背包/进度。动作相同但状态变了就不算循环。"""
    try:
        inv = state.inventory()
        items = tuple(sorted((str(k), int(v)) for k, v in inv.items() if int(v)))
    except Exception:
        items = ()
    pos = state.data.get("pos") or []
    try:
        pos = tuple(round(float(x), 1) for x in pos)
    except (TypeError, ValueError):
        pos = ()
    return {
        "inv": items,
        "pos": pos,
        "adv": len(state.data.get("advancements") or {}),
    }


def action_signature(action, args=None, state=None):
    signature = {"action": str(action or "")}
    if args:
        signature["args"] = args
    if state is not None:
        signature["state"] = state_signature(state)
    return signature

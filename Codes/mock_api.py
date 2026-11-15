# -*- coding: utf-8 -*-
"""
离线假服务器 + 假大脑 —— 不联网也能把整套流程跑通。

用途：
1. `python main.py mock`：自检（不会花 token、不会连比赛服）；
2. 单元测试：验证「采集/合成/砍树/熔炼/决策/状态保存」这些逻辑；
3. 以后改技能时，先在这里跑通，再上真服。

注意：它是**行为近似**，不是真 Minecraft。真实服务器以接口为准。
"""

import copy
import json
import math
import time

from mc_api import McApiError

LOG_TYPES = ("oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log")

RECIPES = {
    # 物品: (材料, 一次产出, 是否需要工作台)
    "oak_planks": ({"oak_log": 1}, 4, False),
    "birch_planks": ({"birch_log": 1}, 4, False),
    "stick": ({"oak_planks": 2}, 4, False),
    "crafting_table": ({"oak_planks": 4}, 1, False),
    "wooden_pickaxe": ({"oak_planks": 3, "stick": 2}, 1, True),
    "wooden_axe": ({"oak_planks": 3, "stick": 2}, 1, True),
    "wooden_sword": ({"oak_planks": 2, "stick": 1}, 1, True),
    "stone_pickaxe": ({"cobblestone": 3, "stick": 2}, 1, True),
    "stone_axe": ({"cobblestone": 3, "stick": 2}, 1, True),
    "stone_sword": ({"cobblestone": 2, "stick": 1}, 1, True),
    "furnace": ({"cobblestone": 8}, 1, True),
    "chest": ({"oak_planks": 8}, 1, True),
    "torch": ({"stick": 1, "coal": 1}, 4, False),
}

DROP = {
    "oak_log": "oak_log", "birch_log": "birch_log", "spruce_log": "spruce_log",
    "stone": "cobblestone", "cobblestone": "cobblestone", "dirt": "dirt",
    "grass_block": "dirt", "coal_ore": "coal", "iron_ore": "raw_iron",
    "oak_leaves": None,
}

SOLID = set(RECIPES) | set(LOG_TYPES) | {
    "stone", "cobblestone", "dirt", "grass_block",
    "coal_ore", "iron_ore", "oak_leaves", "furnace",
}


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class MockApi:
    """行为接近 NMO 接口的假服务器。"""

    def __init__(self, seed=7):
        self.seed = seed
        self.pos = [0.5, 65.0, 0.5]
        self.health = 20.0
        self.food = 20.0
        self.alive = True
        self.items = {}
        self.world = {}
        self.table_placed = False
        self.furnace = None
        self.event_log = []
        self.seq = 0
        self.completed = {}          # id -> frame
        self.token = None
        self._build_world()

    # ---------------- 世界生成 ----------------
    def _build_world(self):
        for x in range(-24, 25):
            for z in range(-24, 25):
                self.world[(x, 64, z)] = "grass_block"
                for y in range(58, 64):
                    self.world[(x, y, z)] = "stone"
        # 几棵树
        for tx, tz, height in ((0, 0, 5), (8, 4, 4), (-7, 6, 5), (6, -8, 4), (-9, -7, 5)):
            for dy in range(height):
                self.world[(tx, 65 + dy, tz)] = "oak_log"
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for dy in (height - 1, height):
                        key = (tx + dx, 65 + dy, tz + dz)
                        if key not in self.world:
                            self.world[key] = "oak_leaves"
        # 一个小采石坑：露出石头（不然 find_blocks 找不到「暴露方块」）
        for x in range(2, 5):
            for z in range(0, 3):
                self.world[(x, 64, z)] = "air"
        # 地表露头矿，方便测试挖矿流程
        self.world[(3, 64, 3)] = "coal_ore"
        self.world[(-3, 64, 2)] = "iron_ore"
        # 一点地下矿（暂时挖不到，留着以后扩展）
        for x, y, z, name in ((3, 58, 3, "coal_ore"), (-4, 57, 2, "iron_ore")):
            self.world[(x, y, z)] = name
        # 出生点脚下挖空，免得站不进树里
        self.world[(0, 65, 0)] = "air"

    # ---------------- 内部 ----------------
    def _block_at(self, pos):
        return self.world.get(
            (math.floor(pos[0]), math.floor(pos[1]), math.floor(pos[2])), "air")

    def _ground_y(self, x, z, from_y=None):
        """从 from_y 往下找第一个实心方块，返回它上面那一格（脚的位置）。"""
        start = int(from_y if from_y is not None else self.pos[1]) + 1
        for y in range(start, 50, -1):
            if self.world.get((math.floor(x), y, math.floor(z))) in SOLID:
                return y + 1
        return 65

    def _count(self, name):
        return int(self.items.get(name, 0))

    def _add(self, name, n=1):
        if n:
            self.items[name] = self._count(name) + int(n)

    def _consume(self, name, n):
        left = self._count(name) - int(n)
        if left > 0:
            self.items[name] = left
        else:
            self.items.pop(name, None)

    def _inventory_dict(self):
        return {k: v for k, v in self.items.items() if v > 0}

    def _has_pickaxe(self):
        return any(name.endswith("_pickaxe") for name in self.items)

    def _emit(self, kind, **fields):
        self.seq += 1
        record = {"seq": self.seq, "t": int(time.time() * 1000), "type": kind}
        record.update(fields)
        self.event_log.append(record)
        self.event_log = self.event_log[-200:]

    def _check_advancements(self):
        checks = [
            ("minecraft:story/mine_stone", lambda: self._count("cobblestone") > 0, "task"),
            ("minecraft:story/upgrade_tools",
             lambda: self._count("wooden_pickaxe") + self._count("stone_pickaxe") > 0, "task"),
            ("minecraft:story/smelt_iron", lambda: self._count("iron_ingot") > 0, "goal"),
        ]
        for adv_id, condition, frame in checks:
            if adv_id not in self.completed and condition():
                self.completed[adv_id] = frame
                self._emit("advancement", id=adv_id)

    # ---------------- 认证 / 连接 ----------------
    def login(self):
        self.token = "mock-token"
        return {"token": self.token, "bot_username": "MockAgent",
                "expires_at": "2099-01-01T00:00:00Z"}

    def logout(self):
        self.token = None
        return {"ok": True}

    def connect(self):
        return {"ok": True, "already_connected": True}

    def disconnect(self):
        return {"ok": True}

    # ---------------- 观察 ----------------
    def status(self):
        return {
            "username": "MockAgent",
            "pos": [round(v, 2) for v in self.pos],
            "yaw": 0.0, "pitch": 0.0,
            "dimension": "overworld", "gamemode": "survival",
            "health": self.health, "food": self.food, "saturation": 5,
            "xp_level": 0, "armor_points": 0, "oxygen": 300,
            "held": None, "offhand": None, "effects": [],
            "on_fire": False, "is_sleeping": False,
            "in_water": False, "submerged": False, "in_lava": False,
            "in_vehicle": None, "sneaking": False, "blocking": False,
            "using_item": None,
            "time_of_day": 1000, "is_day": True, "raining": False,
            "biome": "plains", "on_ground": True,
            "alive": self.alive, "ready": True, "busy": None,
            "last_death": None,
        }

    def inventory(self):
        items = []
        for slot, (name, count) in enumerate(sorted(self._inventory_dict().items())):
            items.append({"name": name, "count": count, "slot": 36 + slot})
        return {
            "items": items,
            "armor": {"head": None, "chest": None, "legs": None, "feet": None},
            "offhand": None, "held": None,
            "empty_slots": max(0, 36 - len(items)),
        }

    def find_blocks(self, types=None, radius=16, limit=20, properties=None, mature=None):
        radius = max(1, min(64, int(radius or 16)))
        limit = max(1, min(200, int(limit or 20)))
        names = None
        if types:
            names = set(types) if isinstance(types, (list, tuple, set)) else {types}
        found = []
        for (x, y, z), name in self.world.items():
            if name == "air":
                continue
            d = _dist([x + 0.5, y + 0.5, z + 0.5], self.pos)
            if d > radius:
                continue
            if names is not None and name not in names:
                continue
            # 只返回暴露方块（六面至少一面是空气）
            exposed = any(
                self.world.get((x + dx, y + dy, z + dz), "air") == "air"
                for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                                   (0, -1, 0), (0, 0, 1), (0, 0, -1))
            )
            if not exposed:
                continue
            found.append({"name": name, "pos": [x, y, z], "dist": round(d, 1),
                          "properties": {}})

        found.sort(key=lambda b: b["dist"])
        if types is None:
            summary = {}
            for block in found:
                item = summary.setdefault(block["name"], {
                    "count": 0, "nearest": block["pos"], "dist": block["dist"]})
                item["count"] += 1
            return {"radius": radius, "scanned": len(found), "blocks": summary}
        return {"total": len(found), "blocks": found[:limit]}

    def get_block(self, pos=None, x=None, y=None, z=None):
        pos = pos or [x, y, z]
        if not pos or len(pos) != 3:
            raise McApiError("bad_args", "坐标不对")
        name = self._block_at(pos)
        return {
            "name": name, "pos": [math.floor(v) for v in pos], "properties": {},
            "diggable": name not in ("air", "bedrock"),
            "hardness": 2 if name in LOG_TYPES else 0.5,
            "harvest_tool_ok": True,
        }

    def get_entities(self, radius=32, types=None):
        return {"entities": []}

    def events(self, since=None, limit=None):
        since = int(since or 0)
        events = [e for e in self.event_log if e["seq"] > since]
        events = events[: int(limit or 50)]
        last = events[-1]["seq"] if events else self.seq
        return {"since": since, "last_seq": last, "total": len(events), "events": events}

    def advancements(self, status=None, include_recipes=None):
        entries = []
        for adv_id, frame in self.completed.items():
            entries.append({
                "id": adv_id, "title": adv_id.split("/")[-1], "frame": frame,
                "status": "completed", "completed_at": 1700000000000, "progress": None,
            })
        for adv_id, frame in (("minecraft:story/mine_diamond", "goal"),
                              ("minecraft:adventure/kill_a_mob", "task"),
                              ("minecraft:husbandry/plant_seed", "task"),
                              ("minecraft:nether/obtain_blaze_rod", "goal")):
            if adv_id not in self.completed:
                entries.append({
                    "id": adv_id, "title": adv_id.split("/")[-1], "frame": frame,
                    "status": "in_progress", "completed_at": None, "progress": None,
                })
        return {"ready": True, "updated_at": 1700000000000, "packets_received": 1,
                "summary": {"completed": len(self.completed)}, "advancements": entries}

    # ---------------- 动作 ----------------
    def move_to(self, pos=None, x=None, y=None, z=None, entity_id=None, range=None):
        if entity_id is not None:
            return {"pos": [round(v, 2) for v in self.pos]}
        if pos:
            tx, tz = float(pos[0]), float(pos[2])
        else:
            tx, tz = float(x), float(z)
        # 目标格上方被方块占住 → 模拟寻路失败（和真服一致）
        if self._block_at([tx, self.pos[1] + 1, tz]) in SOLID:
            raise McApiError("path_not_found", "No path to the goal!")
        feet_y = self._ground_y(tx, tz)
        if self.world.get((math.floor(tx), feet_y, math.floor(tz)), "air") != "air":
            raise McApiError("path_not_found", "No path to the goal!")
        if self.world.get((math.floor(tx), feet_y + 1, math.floor(tz)), "air") != "air":
            raise McApiError("path_not_found", "No path to the goal!")
        self.pos = [round(tx, 2), float(feet_y), round(tz, 2)]
        return {"pos": [round(v, 2) for v in self.pos]}

    def dig(self, pos=None, x=None, y=None, z=None, collect=None):
        pos = pos or [x, y, z]
        key = (math.floor(pos[0]), math.floor(pos[1]), math.floor(pos[2]))
        name = self.world.get(key, "air")
        if name == "air":
            raise McApiError("nothing_to_dig", f"这里没方块：{pos}")
        if name in ("bedrock",):
            raise McApiError("undiggable", "挖不动")
        drop = DROP.get(name, name)
        if name in ("stone", "coal_ore", "iron_ore") and not self._has_pickaxe():
            drop = None
        self.world[key] = "air"
        gained = {}
        if drop and collect is not False:
            self._add(drop, 1)
            gained[drop] = 1
        self._check_advancements()
        return {"dug": name, "dig_ms": 300, "tool": "hand", "gained": gained, "sneaked": False}

    def place(self, item=None, pos=None, x=None, y=None, z=None):
        if not item:
            raise McApiError("bad_args", "缺 item")
        if self._count(item) < 1:
            raise McApiError("missing_item", f"背包里没有 {item}")
        if not pos:
            placed = False
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (2, 0), (0, 2), (1, 1), (-1, -1)):
                base = [math.floor(self.pos[0]) + dx, math.floor(self.pos[1]),
                        math.floor(self.pos[2]) + dz]
                below = (base[0], base[1] - 1, base[2])
                if self.world.get(below) in SOLID and \
                        self.world.get((base[0], base[1], base[2]), "air") == "air":
                    pos = base
                    placed = True
                    break
            if not placed:
                raise McApiError("no_support", "没有合适的位置放方块")
        key = (math.floor(pos[0]), math.floor(pos[1]), math.floor(pos[2]))
        if self.world.get(key, "air") != "air":
            raise McApiError("occupied", "目标位置被占")
        self.world[key] = item
        self._consume(item, 1)
        if item == "crafting_table":
            self.table_placed = True
        if item == "furnace":
            self.furnace = {"pos": list(pos), "input": {}, "fuel": 0, "output": {}}
        return {"placed": item, "pos": [math.floor(v) for v in pos], "sneaked": True}

    def interact(self, pos=None, x=None, y=None, z=None, entity_id=None, item=None):
        return {"block": self._block_at(pos) if pos else "air", "changed": False}

    def use_item(self, item=None, target=None, ms=None, timeout=None, source=None):
        if not item:
            raise McApiError("bad_args", "缺 item")
        if self._count(item) < 1:
            raise McApiError("missing_item", f"没有 {item}")
        self._consume(item, 1)
        if item in ("bread", "apple", "cooked_beef", "carrot", "baked_potato"):
            self.food = min(20.0, self.food + 4)
            return {"used": item, "food": self.food, "health": self.health}
        return {"used": item}

    def attack(self, entity_id=None, timeout=None):
        return {"killed": True, "hits": 3, "ms": 500, "inventory": self._inventory_dict()}

    def shoot(self, pos=None, entity_id=None, weapon=None, charge_ms=None):
        return {"fired": True, "weapon": weapon or "bow", "used": {"arrow": 1}}

    def craft(self, item=None, count=None):
        if not item or item not in RECIPES:
            raise McApiError("no_recipe", f"没有配方：{item}")
        materials, out_per, needs_table = RECIPES[item]
        count = max(1, int(count or 1))
        crafts = int(math.ceil(count / float(out_per)))
        if needs_table and not self.table_placed:
            raise McApiError("need_crafting_table", f"需要工作台才能合成 {item}")
        missing = {name: n * crafts for name, n in materials.items()
                   if self._count(name) < n * crafts}
        if missing:
            raise McApiError(
                "missing_ingredients",
                f"材料不够：{missing}",
                {"example_recipe_needs": materials, "inventory": self._inventory_dict()},
            )
        for name, n in materials.items():
            self._consume(name, n * crafts)
        produced = out_per * crafts
        self._add(item, produced)
        self._check_advancements()
        return {"crafted": item, "gained": produced, "used_table": needs_table,
                "inventory": self._inventory_dict()}

    def window_view(self, target=None, **kwargs):
        if not self.furnace:
            raise McApiError("no_such_entity", "附近没有熔炉")
        output = [{"name": name, "count": n} for name, n in self.furnace["output"].items()]
        return {"window": "furnace", "target": "furnace",
                "sections": {"input": [], "fuel": [], "output": output},
                "info": {"burning": bool(output), "progress": 0.5, "fuel_left": 0.5}}

    def window_act(self, target=None, ops=None, **kwargs):
        if not self.furnace:
            raise McApiError("no_such_entity", "附近没有熔炉")
        for op in ops or []:
            kind = op.get("op")
            if kind == "put":
                item = op.get("item")
                n = int(op.get("count") or 1)
                if self._count(item) < n:
                    raise McApiError("missing_item", f"没有 {item}")
                self._consume(item, n)
                if op.get("to") == "fuel":
                    self.furnace["fuel"] = self.furnace.get("fuel", 0) + n
                else:
                    self.furnace["input"][item] = self.furnace["input"].get(item, 0) + n
            elif kind == "take":
                for name in list(self.furnace["output"].keys()):
                    n = self.furnace["output"][name]
                    self._add(name, n)
                    self.furnace["output"].pop(name, None)
        # 简化：有燃料就立刻烧好
        if self.furnace["fuel"] > 0 and self.furnace["input"]:
            for name, n in list(self.furnace["input"].items()):
                cooked = {"raw_iron": "iron_ingot", "raw_gold": "gold_ingot",
                          "sand": "glass"}.get(name, name)
                self.furnace["output"][cooked] = self.furnace["output"].get(cooked, 0) + n
                self.furnace["input"].pop(name, None)
            self._check_advancements()
        return {"window": "furnace", "done": True}

    def equip(self, item=None, slot=None):
        if not item or self._count(item) < 1:
            raise McApiError("missing_item", f"没有 {item}")
        if slot in (None, "hand"):
            return {"held": {"name": item, "count": 1, "slot": 36}}
        return {"equipped": {"name": item, "count": 1}}

    def posture(self, sneak=None, shield=None):
        return {"sneaking": bool(sneak), "blocking": bool(shield), "offhand": None}

    def dismount(self):
        raise McApiError("not_in_vehicle", "不在载具上")

    def drop(self, item=None, count=None):
        if not item or self._count(item) < 1:
            raise McApiError("missing_item", f"没有 {item}")
        self._consume(item, int(count or 1))
        return {"inventory": self._inventory_dict()}

    def chat(self, message=None):
        return {"sent": True}

    def stop(self):
        return {"stopped": True}


# ---------------------------------------------------------------------
# 假大脑：按剧本返回决策，用来离线跑通主循环
# ---------------------------------------------------------------------
DEFAULT_SCRIPT = [
    {"skill": "chop_tree", "args": {}, "target": "", "why": "先弄木头"},
    {"skill": "chop_tree", "args": {}, "target": "", "why": "再弄点木头"},
    {"skill": "craft", "args": {"item": "wooden_pickaxe", "count": 1}, "target": "", "why": "做镐"},
    {"skill": "collect", "args": {"group": "stone", "count": 8}, "target": "", "why": "挖石头"},
    {"skill": "craft", "args": {"item": "stone_pickaxe", "count": 1}, "target": "", "why": "石镐"},
    {"skill": "craft", "args": {"item": "crafting_table", "count": 1}, "target": "", "why": "工作台"},
    {"skill": "place", "args": {"item": "crafting_table"}, "target": "", "why": "放下工作台"},
    {"skill": "collect", "args": {"group": "coal", "count": 1}, "target": "", "why": "挖煤"},
    {"skill": "scan", "args": {}, "target": "", "why": "看看周围"},
]


class ScriptedLLM:
    """假大脑：不需要网络，按剧本输出 JSON；剧本跑完就重复最后一条。"""

    def __init__(self, script=None, name="ScriptedLLM"):
        self.script = copy.deepcopy(script or DEFAULT_SCRIPT)
        self.index = 0
        self.name = name
        self.usage = {"input": 0, "output": 0, "calls": 0}
        self.on_usage = None

    def chat(self, messages, **kwargs):
        return json.dumps(self.chat_json(messages), ensure_ascii=False)

    def chat_json(self, messages, **kwargs):
        decision = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1
        self.usage["input"] += 900
        self.usage["output"] += 60
        self.usage["calls"] += 1
        if self.on_usage:
            self.on_usage(self.usage)
        return copy.deepcopy(decision)

    def remaining_input(self):
        return 8_000_000 - self.usage["input"]

    def usage_text(self):
        return (f"输入 {self.usage['input']:,}，输出 {self.usage['output']:,}，"
                f"调用 {self.usage['calls']} 次")

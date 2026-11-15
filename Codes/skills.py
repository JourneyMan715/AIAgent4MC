# -*- coding: utf-8 -*-
"""
技能层（Skills）—— 把「一堆底层 API 调用」压成「一个像人一样的动作」。

为什么要这一层（比赛的命门）：
- 旧版是「挖一个方块问一次大模型」，8 小时能问的次数有限、token 也烧不起；
- 现在把这些固定套路写成**确定性代码**（找树 → 走到树下 → 自下而上挖 → 确认背包增量），
  大模型只需要说一句 `chop_tree()`，一次决策能顶 20~50 次动作。
- 技能里自带重试、失败降级、黑名单，模型不用操心细节。

每个技能签名统一：fn(api, state, **参数) -> dict（必须含 "summary"）
失败统一抛 SkillError（带 code），由主循环决定要不要重试/换招。
"""

import math
import random
import time

from mc_api import McApiError


class SkillError(RuntimeError):
    """技能执行失败（不是网络/接口错误，而是「这步没做成」）。"""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------
# 方块分组：让模型说 "log" 就行，不用背 6 种原木的名字
# ---------------------------------------------------------------------
LOG_TYPES = [
    "oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log",
    "mangrove_log", "cherry_log", "pale_oak_log", "oak_wood", "birch_wood", "spruce_wood",
]
STONE_TYPES = ["stone", "cobblestone", "deepslate", "tuff", "andesite", "diorite", "granite"]
GROUPS = {
    "log": LOG_TYPES,
    "wood": LOG_TYPES,
    "stone": STONE_TYPES,
    "dirt": ["dirt", "grass_block", "coarse_dirt", "podzol"],
    "sand": ["sand", "red_sand", "gravel"],
    "coal": ["coal_ore", "deepslate_coal_ore"],
    "iron": ["iron_ore", "deepslate_iron_ore"],
    "copper": ["copper_ore", "deepslate_copper_ore"],
    "gold": ["gold_ore", "deepslate_gold_ore", "nether_gold_ore"],
    "diamond": ["diamond_ore", "deepslate_diamond_ore"],
    "redstone": ["redstone_ore", "deepslate_redstone_ore"],
    "lapis": ["lapis_ore", "deepslate_lapis_ore"],
    "emerald": ["emerald_ore", "deepslate_emerald_ore"],
}

FOODS = [
    "cooked_beef", "cooked_porkchop", "cooked_mutton", "cooked_chicken", "cooked_rabbit",
    "cooked_salmon", "cooked_cod", "bread", "baked_potato", "apple", "golden_carrot",
    "carrot", "potato", "beetroot", "melon_slice", "sweet_berries", "beef", "porkchop",
    "mutton", "chicken", "rabbit", "cod", "salmon", "cookie", "pumpkin_pie",
]

TOOL_TIERS = ["netherite", "diamond", "iron", "stone", "golden", "wooden"]

# 虚空岛赛制（RULES.md v2）：岛是随机截取的一块，能不能做某些进度取决于岛上有什么。
# 这些是「一眼能认出来」的特征方块，用来在开局做一次勘察。
SIGNATURE_BLOCKS = {
    "village": ("bell", "composter", "hay_block", "smithing_table", "fletching_table"),
    "fortress": ("nether_bricks", "nether_brick_fence", "blaze_spawner"),
    "bastion": ("blackstone", "gilded_blackstone", "polished_blackstone_bricks"),
    "trial_chamber": ("trial_spawner", "vault", "crafter", "copper_bulb"),
    "ancient_city": ("sculk_catalyst", "sculk_shrieker", "reinforced_deepslate"),
    "stronghold": ("end_portal_frame", "infested_stone", "stone_bricks"),
    "mineshaft": ("rail", "cobweb", "oak_fence"),
    "ocean_ruins": ("sea_pickle", "prismarine", "sea_lantern"),
    "amethyst_geode": ("amethyst_cluster", "budding_amethyst", "calcite"),
    "coral_reef": ("tube_coral", "brain_coral", "fire_coral", "bubble_coral", "horn_coral"),
    "desert": ("sand", "cactus", "dead_bush"),
    "snowy": ("snow_block", "ice", "powder_snow"),
    "jungle": ("bamboo", "jungle_log", "cocoa"),
    "swamp": ("lily_pad", "mangrove_roots", "mud"),
}

# 虚空岛：不要走太远，掉下去就没了
MAX_EXPLORE_DISTANCE = 24


# ---------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------
def _now():
    return time.time()


def _is_log(name):
    return bool(name) and (name.endswith("_log") or name.endswith("_wood") or name.endswith("_stem"))


def _inv(api, state=None, force=False):
    """背包 {名字: 数量}，带缓存（默认 6 秒）。"""
    if state is not None and not force and state.inventory() and state.inventory_age() < 6:
        return state.inventory()
    result = api.inventory()
    if state is not None:
        state.update_inventory(result)
        return state.inventory()
    counts = {}
    for item in (result or {}).get("items") or []:
        if isinstance(item, dict) and item.get("name"):
            counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
    return counts


def _block(api, pos):
    """查一个方块，查不到就返回 None（区块没加载、坐标不合法都不抛错）。"""
    try:
        return api.get_block(pos=pos)
    except McApiError:
        return None


def _dist2(a, b):
    if not a or not b:
        return 9999.0
    return ((a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _key(name, pos):
    return f"{name}@{int(pos[0])},{int(pos[1])},{int(pos[2])}"


def _summary(text, **detail):
    data = {"summary": text}
    data.update(detail)
    return data


# ---------------------------------------------------------------------
# 定位 / 移动
# ---------------------------------------------------------------------
def approach(api, pos, rng=2):
    """走到方块旁边（只按水平坐标走，避免「走到方块内部」导致无路可走）。"""
    x, _, z = pos
    try:
        return api.move_to(x=x + 0.5, z=z + 0.5, range=rng)
    except McApiError as e:
        if e.code not in ("path_not_found", "timeout", "out_of_reach", "out_of_range", "blocked"):
            raise
    # 目标点站不下（树里、水面上……）：试四个相邻格
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
        try:
            return api.move_to(x=x + 0.5 + dx * 1.5, z=z + 0.5 + dz * 1.5, range=max(1, rng))
        except McApiError:
            continue
    raise SkillError("path_not_found", f"走不到 ({x}, {z}) 附近")


# ---------------------------------------------------------------------
# 技能：观察
# ---------------------------------------------------------------------
def scan(api, state, radius=24, **_):
    """周围有什么：方块种类摘要 + 生物。"""
    blocks = {}
    try:
        result = api.find_blocks(radius=radius) or {}
        raw = result.get("blocks")
        if isinstance(raw, dict):                     # 摘要模式
            top = sorted(raw.items(), key=lambda kv: -(kv[1] or {}).get("count", 0))[:8]
            blocks = {name: (info or {}).get("count", 0) for name, info in top}
        elif isinstance(raw, list):
            for b in raw[:12]:
                blocks[b.get("name")] = blocks.get(b.get("name"), 0) + 1
    except McApiError as e:
        blocks = {"_error": e.code}

    entities = []
    try:
        result = api.get_entities(radius=radius, types=["hostile", "animal", "player"]) or {}
        for ent in (result.get("entities") or [])[:8]:
            entities.append(f"{ent.get('name')}×{ent.get('dist')}")
    except McApiError:
        pass

    pos = state.data.get("pos")
    text = f"附近方块 {blocks}；生物 {entities or '无'}；坐标 {pos}"
    return _summary(text, blocks=blocks, entities=entities)


# ---------------------------------------------------------------------
# 技能：采集
# ---------------------------------------------------------------------
def _resolve_types(group, types):
    if types:
        if isinstance(types, str):
            return [types]
        return [str(t) for t in types]
    if group:
        key = str(group).lower()
        if key in GROUPS:
            return GROUPS[key]
        return [key]
    return LOG_TYPES


def collect(api, state, group=None, types=None, count=8, radius=32, limit=20, **_):
    """采集某种方块若干个（木头/石头/矿）。会自动走过去、挖、确认掉落。"""
    names = _resolve_types(group, types)
    want = max(1, int(count or 1))
    limit = max(1, min(60, int(limit or 20)))
    radius = max(1, min(64, int(radius or 32)))
    total = {}
    actions = 0
    target = None

    for _ in range(limit):
        gathered = sum(total.values())
        if gathered >= want:
            break
        try:
            found = api.find_blocks(types=names, radius=radius, limit=6) or {}
            blocks = found.get("blocks") or []
            if not isinstance(blocks, list) or not blocks:
                break
            blocks = [b for b in blocks if not state.is_blacklisted(_key("dig", b["pos"]))]
            if not blocks:
                break
            target = blocks[0]
            approach(api, target["pos"])
            result = api.dig(pos=target["pos"], collect=True) or {}
            actions += 1
            gained = result.get("gained") or {}
            if gained:
                for name, n in gained.items():
                    total[name] = total.get(name, 0) + int(n)
                state.clear_failure(_key("dig", target["pos"]))
            else:
                # 挖到了但没进背包（掉落物没捡到 / 本来就不掉）：记一笔，换下一个
                state.record_failure(_key("dig", target["pos"]))
        except McApiError as e:
            if e.code in ("wrong_server", "not_connected", "dead", "death_loop_cooldown",
                          "busy", "interrupted"):
                raise SkillError(e.code, e.message)
            if target:
                state.record_failure(_key("dig", target.get("pos", [0, 0, 0])))
            continue

    if not total:
        raise SkillError("nothing_collected", f"没挖到 {names[:3]}…（可能附近没有/走不到）")
    text = "采集 " + "、".join(f"{k}×{v}" for k, v in total.items())
    return _summary(text, gained=total, actions=actions)


def chop_tree(api, state, pos=None, max_logs=8, **_):
    """砍一整棵树（自动找树干最下面一节，自下而上挖）。"""
    if not pos:
        found = api.find_blocks(types=LOG_TYPES, radius=32, limit=5) or {}
        blocks = found.get("blocks") or []
        if not blocks:
            raise SkillError("no_tree", "32 格内没找到树")
        pos = blocks[0]["pos"]

    x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
    base = [x, y, z]
    for _ in range(12):                     # 往下找树根
        below = _block(api, [base[0], base[1] - 1, base[2]])
        if below and _is_log(below.get("name", "")):
            base[1] -= 1
        else:
            break

    approach(api, base)
    gained = {}
    action = 0
    for i in range(int(max_logs or 8)):
        p = [base[0], base[1] + i, base[2]]
        block = _block(api, p)
        if not block or not _is_log(block.get("name", "")):
            break
        try:
            result = api.dig(pos=p, collect=True) or {}
        except McApiError as e:
            if e.code in ("out_of_reach", "nothing_to_dig", "undiggable"):
                break
            raise
        action += 1
        for name, n in (result.get("gained") or {}).items():
            gained[name] = gained.get(name, 0) + int(n)

    if not gained:
        raise SkillError("chop_failed", "树没砍下来（可能整棵被挡/掉落物没捡到）")
    text = "砍树 " + "、".join(f"{k}×{v}" for k, v in gained.items())
    return _summary(text, gained=gained, actions=action)


# ---------------------------------------------------------------------
# 技能：合成（带递归补料）
# ---------------------------------------------------------------------
def _needs_from_extra(extra):
    """从 missing_ingredients 错误里解析出「还缺什么」。"""
    raw = None
    if isinstance(extra, dict):
        # 新服务端会给 missing_total（按本次请求数量算出的缺口）；
        # 旧服务端/比赛服只有 example_recipe_needs（单次合成的用料）
        raw = extra.get("missing_total") or extra.get("example_recipe_needs")
    if raw is None:
        return {}
    needs = {}
    if isinstance(raw, dict):
        for name, n in raw.items():
            try:
                needs[str(name)] = max(1, int(n))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str):
                needs[item] = 1
            elif isinstance(item, dict):
                name = item.get("name") or item.get("item")
                try:
                    n = int(item.get("count", item.get("quantity", 1)))
                except (TypeError, ValueError):
                    n = 1
                if name:
                    needs[str(name)] = max(1, n)
    return needs


def ensure_table(api, state, depth=0):
    """需要 3x3 合成时，确保身边有一张工作台。"""
    table_pos = (state.data.get("waypoints") or {}).get("crafting_table")
    if table_pos:
        block = _block(api, table_pos)
        if block and block.get("name") == "crafting_table":
            return table_pos
    inv = _inv(api, state)
    if int(inv.get("crafting_table", 0)) < 1:
        craft_chain(api, state, "crafting_table", 1, depth + 1)
    result = api.place(item="crafting_table") or {}
    placed = result.get("pos")
    if placed:
        state.add_waypoint("crafting_table", placed)
    return placed


def craft_chain(api, state, item, count=1, depth=0, attempt=0, **_):
    """合成物品；缺材料就按服务器给的配方递归补齐，需要工作台就放一张。

    难的是「中间产物会抢原料」（比如做木镐要 3 木板 + 2 木棍，而木棍又要吃 2 木板）。
    所以这里做成**循环补料**：每次缺料就重新拉背包、把缺的补到够，最多试 4 轮。
    """
    if not item:
        raise SkillError("bad_args", "craft 需要 item")
    if depth > 5:
        raise SkillError("craft_too_deep", f"合成链太深：{item}")
    if attempt > 4:
        raise SkillError("craft_failed", f"合成 {item} 反复缺料，放弃")
    count = max(1, int(count or 1))

    try:
        result = api.craft(item=str(item), count=count) or {}
        inv = result.get("inventory")
        if isinstance(inv, dict):
            state.data["inventory"] = {k: int(v) for k, v in inv.items()}
            state.data["inventory_at"] = _now()
        text = f"合成 {result.get('crafted', item)}×{result.get('gained', count)}"
        return _summary(text, crafted=item, gained=result.get("gained", count),
                        used_table=result.get("used_table"))
    except McApiError as e:
        if e.code == "missing_ingredients":
            total_mode = isinstance(e.extra, dict) and isinstance(e.extra.get("missing_total"), dict)
            needs = _needs_from_extra(e.extra)
            if not needs:
                raise SkillError("missing_ingredients", f"合成 {item} 缺料：{e.message}")
            inv = _inv(api, state, force=True)
            progressed = False
            for name, n in needs.items():
                if total_mode:
                    # missing_total 已经是「还差多少」，直接补这个数
                    craft_chain(api, state, name, max(1, int(n)), depth + 1)
                    progressed = True
                else:
                    need_total = max(1, n * count)
                    have = int(inv.get(name, 0))
                    if have < need_total:
                        craft_chain(api, state, name, need_total - have, depth + 1)
                        progressed = True
                inv = _inv(api, state, force=True)
            if not progressed:
                raise SkillError("missing_ingredients", f"合成 {item} 缺料：{e.message}")
            return craft_chain(api, state, item, count, depth, attempt + 1)

        if e.code == "need_crafting_table":
            if item == "crafting_table":
                # crafting_table 是 2x2 配方，不需要工作台；服务器这么说多半是缺木板
                raise SkillError("missing_ingredients", "合成工作台不需要工作台，应该是缺木板")
            if attempt > 2:
                raise SkillError("need_crafting_table", f"合成 {item} 需要工作台，但放不下")
            ensure_table(api, state, depth)
            return craft_chain(api, state, item, count, depth, attempt + 1)

        raise SkillError(e.code, e.message)


# ---------------------------------------------------------------------
# 技能：熔炼（比较慢，失败不致命）
# ---------------------------------------------------------------------
def _pick_fuel(inv):
    for name in ("coal", "charcoal", "coal_block", "oak_planks", "birch_planks",
                 "spruce_planks", "oak_log", "birch_log"):
        if int(inv.get(name, 0)) > 0:
            return name
    return None


def smelt(api, state, item, count=1, fuel=None, **_):
    """烧东西：放燃料 + 放原料 → 等 → 取成品。"""
    if not item:
        raise SkillError("bad_args", "smelt 需要 item")
    count = max(1, int(count or 1))

    furnace_pos = (state.data.get("waypoints") or {}).get("furnace")
    if furnace_pos:
        block = _block(api, furnace_pos)
        if not block or block.get("name") not in ("furnace", "blast_furnace"):
            furnace_pos = None
    if not furnace_pos:
        found = api.find_blocks(types=["furnace", "blast_furnace"], radius=16, limit=1) or {}
        blocks = found.get("blocks") or []
        if blocks:
            furnace_pos = blocks[0]["pos"]
        else:
            inv = _inv(api, state)
            if int(inv.get("furnace", 0)) < 1:
                craft_chain(api, state, "furnace", 1)
            result = api.place(item="furnace") or {}
            furnace_pos = result.get("pos")
            if not furnace_pos:
                raise SkillError("place_failed", "熔炉放不下")
        state.add_waypoint("furnace", furnace_pos)

    inv = _inv(api, state)
    fuel = fuel or _pick_fuel(inv)
    if not fuel or int(inv.get(fuel, 0)) < 1:
        raise SkillError("missing_fuel", "没有燃料（煤/木炭/木板/原木）")
    fuel_count = max(1, (count + 7) // 8)

    approach(api, furnace_pos, rng=2)
    api.window_act(target={"pos": furnace_pos}, ops=[
        {"op": "put", "item": fuel, "count": fuel_count, "to": "fuel"},
        {"op": "put", "item": item, "count": count, "to": "input"},
    ])

    deadline = _now() + count * 12 + 20
    got = 0
    while _now() < deadline:
        time.sleep(4)
        try:
            view = api.window_view(target={"pos": furnace_pos}) or {}
        except McApiError:
            break
        output = ((view.get("sections") or {}).get("output") or [])
        got = sum(int(i.get("count") or 0) for i in output if isinstance(i, dict))
        if got >= count:
            break

    try:
        api.window_act(target={"pos": furnace_pos},
                       ops=[{"op": "take", "from": "output", "count": count}])
    except McApiError as e:
        raise SkillError(e.code, f"取成品失败：{e.message}")

    text = f"熔炼 {item}×{count}（产出约 {got}）"
    return _summary(text, item=item, count=count)


# ---------------------------------------------------------------------
# 技能：生存 / 装备 / 战斗
# ---------------------------------------------------------------------
def eat(api, state, item=None, **_):
    """吃东西回饥饿。"""
    inv = _inv(api, state)
    candidates = [item] if item else FOODS
    for name in candidates:
        if name and int(inv.get(name, 0)) > 0:
            result = api.use_item(item=name) or {}
            food = result.get("food", state.data.get("food"))
            text = f"吃了 {name}（饥饿 {food}）"
            state.data["food"] = food if food is not None else state.data.get("food")
            return _summary(text, item=name, food=food)
    raise SkillError("no_food", "背包里没有能吃的")


def equip(api, state, item=None, slot="hand", **_):
    """装备手上/护甲；不指定就挑最好的工具。"""
    inv = _inv(api, state)
    if not item:
        for kind in ("pickaxe", "axe", "sword", "shovel"):
            for tier in TOOL_TIERS:
                candidate = f"{tier}_{kind}"
                if int(inv.get(candidate, 0)) > 0:
                    item = candidate
                    break
            if item:
                break
    if not item or int(inv.get(item, 0)) < 1:
        raise SkillError("missing_item", "背包里没有可装备的")
    result = api.equip(item=item, slot=slot) or {}
    state.data["held"] = item
    return _summary(f"装备 {item} 到 {slot}", held=result)


def place(api, state, item, pos=None, **_):
    """放方块（不给坐标时接口会自动挑一个能放的位置）。"""
    if not item:
        raise SkillError("bad_args", "place 需要 item")
    body = {"item": item}
    if pos:
        body["pos"] = pos
    result = api.place(**body) or {}
    placed = result.get("pos")
    if item in ("crafting_table", "furnace", "chest"):
        state.add_waypoint(item, placed or state.data.get("pos") or [0, 0, 0])
    return _summary(f"放下 {item} 于 {placed}", pos=placed)


def attack(api, state, entity_id=None, timeout=20000, **_):
    """打最近的怪（或指定实体）。"""
    if entity_id is None:
        found = api.get_entities(radius=20, types=["hostile"]) or {}
        ents = found.get("entities") or []
        if not ents:
            raise SkillError("no_target", "20 格内没有敌对生物")
        entity_id = ents[0]["id"]
    result = api.attack(entity_id=int(entity_id), timeout=int(timeout)) or {}
    killed = result.get("killed")
    text = f"{'击杀' if killed else '攻击'} 实体 {entity_id}（命中 {result.get('hits', '?')} 次）"
    return _summary(text, killed=killed)


def flee(api, state, distance=18, entity_id=None, include_players=False, **_):
    """远离最近的威胁（默认只躲怪；被打时可带上玩家）。"""
    pos = state.data.get("pos")
    if not pos:
        raise SkillError("no_pos", "不知道自己在哪")
    types = ["hostile", "player"] if include_players else ["hostile"]
    found = api.get_entities(radius=24, types=types) or {}
    ents = found.get("entities") or []
    if entity_id is not None:
        ents = [e for e in ents if e.get("id") == entity_id]
    if not ents:
        return _summary("附近没有威胁，不用跑")
    threat = ents[0]
    tpos = threat.get("pos") or pos
    dx, dz = pos[0] - tpos[0], pos[2] - tpos[2]
    norm = max(1e-6, (dx * dx + dz * dz) ** 0.5)
    tx = pos[0] + dx / norm * float(distance)
    tz = pos[2] + dz / norm * float(distance)
    api.move_to(x=round(tx, 1), z=round(tz, 1), range=2)
    return _summary(f"躲开 {threat.get('name')}，撤到 ({round(tx)}, {round(tz)})")


def explore(api, state, distance=24, direction=None, **_):
    """朝一个方向走一小段（虚空岛赛制，默认最多 24 格，别掉下去）。"""
    pos = state.data.get("pos")
    if not pos:
        raise SkillError("no_pos", "不知道自己在哪")
    if direction:
        table = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
        dx, dz = table.get(str(direction).lower(), (0, 1))
    else:
        angle = random.uniform(0, 6.283)
        dx, dz = math.cos(angle), math.sin(angle)
    dist = min(float(distance or MAX_EXPLORE_DISTANCE), float(MAX_EXPLORE_DISTANCE))
    tx = round(pos[0] + dx * dist, 1)
    tz = round(pos[2] + dz * dist, 1)
    api.move_to(x=tx, z=tz, range=2)
    return _summary(f"探索到 ({tx}, {tz})")


def survey(api, state, radius=48, **_):
    """开局勘察：看看自己的虚空岛上有什么结构/群系（决定能冲哪些进度）。"""
    radius = max(8, min(64, int(radius or 48)))
    summary = api.find_blocks(radius=radius) or {}
    raw = summary.get("blocks")
    counts = {}
    if isinstance(raw, dict):
        for name, info in raw.items():
            counts[name] = int((info or {}).get("count", 0) or 0)

    features = {}
    for feature, names in SIGNATURE_BLOCKS.items():
        hit = {name: counts[name] for name in names if counts.get(name)}
        if hit:
            features[feature] = hit

    top = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:12])
    island = {
        "radius": radius,
        "features": features,
        "top": top,
        "biome": state.data.get("biome"),
        "at": _now(),
    }
    state.data["island"] = island
    state.save()

    feature_text = "、".join(features) if features else "没有明显结构"
    text = f"勘察 {radius} 格：{feature_text}"
    return _summary(text, island=island)


# ---------------------------------------------------------------------
# 技能注册表
# ---------------------------------------------------------------------
SKILLS = {
    "survey": {
        "fn": survey,
        "help": "勘察自己的虚空岛：识别村庄/要塞/堡垒/试炼密室/古城等结构（决定能冲哪些进度）",
        "args": "radius=48",
    },
    "scan": {
        "fn": scan,
        "help": "观察周围：方块种类 + 生物 + 坐标",
        "args": "radius=24",
    },
    "chop_tree": {
        "fn": chop_tree,
        "help": "砍一整棵树（自动找树干、走到树下、自下而上挖）",
        "args": "pos=[x,y,z]（可选，不给就找最近的树）, max_logs=8",
    },
    "collect": {
        "fn": collect,
        "help": "采集指定方块若干个（木头/石头/煤/铁/钻石…）",
        "args": 'group="log"|"stone"|"coal"|"iron"|"diamond"… 或 types=["oak_log"], count=8, radius=32',
    },
    "craft": {
        "fn": craft_chain,
        "help": "合成物品（缺材料会自动补、需要工作台会自动放）",
        "args": 'item="wooden_pickaxe", count=1',
    },
    "smelt": {
        "fn": smelt,
        "help": "熔炼（铁锭/金锭/玻璃/食物）；需要燃料",
        "args": 'item="raw_iron", count=3, fuel="coal"（可选）',
    },
    "eat": {
        "fn": eat,
        "help": "吃东西回饥饿",
        "args": "item（可选，不给就自动挑）",
    },
    "equip": {
        "fn": equip,
        "help": "装备工具/护甲（不指定就挑最好的）",
        "args": 'item="iron_pickaxe", slot="hand"',
    },
    "place": {
        "fn": place,
        "help": "放一个方块（工作台/熔炉/箱子…）",
        "args": 'item="crafting_table", pos（可选）',
    },
    "attack": {
        "fn": attack,
        "help": "攻击最近的怪（或指定实体）直到死亡/超时",
        "args": "entity_id（可选）, timeout=20000",
    },
    "flee": {
        "fn": flee,
        "help": "远离最近的怪",
        "args": "distance=18",
    },
    "explore": {
        "fn": explore,
        "help": "朝某个方向走一小段（虚空岛：最多 24 格，别掉下去）",
        "args": 'distance≤24, direction="north"|"south"|"east"|"west"（可选）',
    },
}


def skill_help(only=None):
    """给大模型看的技能清单（尽量短，省 token）；only 可限定子集。"""
    lines = []
    for name, spec in SKILLS.items():
        if only and name not in only:
            continue
        lines.append(f"- {name}({spec['args']})：{spec['help']}")
    return "\n".join(lines)


def run_skill(api, state, name, args=None):
    """执行一个技能，统一错误处理。返回 dict（含 summary）。"""
    spec = SKILLS.get((name or "").strip().lower())
    if spec is None:
        raise SkillError("unknown_skill", f"没有这个技能：{name}（可用：{', '.join(SKILLS)}）")
    payload = dict(args or {})
    if not isinstance(payload, dict):
        raise SkillError("bad_args", f"{name} 的 args 必须是对象")
    try:
        result = spec["fn"](api, state, **payload)
    except TypeError as e:
        raise SkillError("bad_args", f"{name} 参数不对：{e}")
    except McApiError as e:
        raise SkillError(e.code, e.message)
    if not isinstance(result, dict):
        result = {"summary": str(result)}
    result.setdefault("summary", f"{name} 完成")
    return result

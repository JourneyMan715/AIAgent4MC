# -*- coding: utf-8 -*-
"""
Advancement 计分表 —— 决定 Agent「先做什么才划算」。

规则（初赛·草案 v2，见 `Doc/RULES.md`）：
    每项分值**只由难度等级决定**：1/2/3/4/5 → 5/15/30/60/100 分
    各分类的根节点（xxx/root）0 分
    只统计计分表里标为「计分」的原版 advancement；标「—」的不计分

    ⚠️ 早期的公开版本曾是「难度基础分 + 5×进度树深度」，v2 草案已经取消深度加成。
    本文件的 `score()` 以数据表里的 `score` 为准。

`Codes/data/advancement_scores.json` 是从 RULES.md 自动生成的官方表（99 项计分，总分 3385）；
文件不存在时才退回下面内置的近似表。
"""

import json
import os

BASE_SCORE = {1: 5, 2: 15, 3: 30, 4: 60, 5: 100}

# 没收录的 advancement，按它的 frame 估一个难度
FRAME_DIFFICULTY = {"task": 1, "goal": 3, "challenge": 4}

# 内置近似表：id -> (难度, 深度, 一句话提示)
CURATED = {
    # ---------- story ----------
    "minecraft:story/root": (1, 0, "故事线起点（0 分）"),
    "minecraft:story/mine_stone": (1, 1, "挖石头"),
    "minecraft:story/upgrade_tools": (1, 2, "做木镐 → 升级石镐"),
    "minecraft:story/smelt_iron": (2, 3, "挖铁、熔炼铁锭"),
    "minecraft:story/obtain_armor": (2, 4, "穿一套铁甲"),
    "minecraft:story/iron_tools": (2, 4, "做铁镐/铁剑"),
    "minecraft:story/deflect_arrow": (3, 5, "用盾牌挡箭"),
    "minecraft:story/form_obsidian": (3, 5, "水+岩浆造黑曜石"),
    "minecraft:story/mine_diamond": (3, 5, "挖到钻石"),
    "minecraft:story/enchant_item": (3, 6, "附魔一件东西"),
    "minecraft:story/enter_the_nether": (4, 2, "造地狱门进下界"),
    "minecraft:story/return_to_sender": (4, 3, "把恶魂火球打回去"),
    "minecraft:story/cure_zombie_villager": (4, 3, "治僵尸村民"),
    "minecraft:story/follow_ender_eye": (4, 3, "跟着末影之眼找要塞"),
    "minecraft:story/enter_the_end": (5, 4, "进末地"),

    # ---------- nether ----------
    "minecraft:nether/root": (1, 0, "下界起点（0 分）"),
    "minecraft:nether/fast_travel": (3, 1, "下界通道快速移动"),
    "minecraft:nether/obtain_blaze_rod": (4, 1, "打烈焰人拿烈焰棒"),
    "minecraft:nether/brew_potion": (3, 2, "酿造药水"),
    "minecraft:nether/find_bastion": (4, 1, "找到堡垒遗迹"),
    "minecraft:nether/charge_respawn_anchor": (4, 2, "给重生锚充能"),
    "minecraft:nether/explore_nether": (4, 1, "走遍下界五个生物群系"),
    "minecraft:nether/create_beacon": (5, 3, "造信标"),
    "minecraft:nether/summon_wither": (5, 2, "召唤凋灵"),
    "minecraft:nether/all_potions": (5, 3, "集齐所有药水"),
    "minecraft:nether/all_effects": (5, 4, "同时拥有所有效果"),

    # ---------- end ----------
    "minecraft:end/root": (1, 0, "末地起点（0 分）"),
    "minecraft:end/kill_dragon": (5, 1, "击杀末影龙"),
    "minecraft:end/dragon_egg": (4, 2, "拿到龙蛋"),
    "minecraft:end/enter_end_gateway": (4, 2, "进入末地折跃门"),
    "minecraft:end/find_end_city": (4, 2, "找到末地城"),
    "minecraft:end/elytra": (4, 3, "拿到鞘翅"),
    "minecraft:end/levitate": (4, 2, "被潜影贝打浮空"),
    "minecraft:end/respawn_dragon": (5, 3, "复活并再杀一次龙"),

    # ---------- adventure ----------
    "minecraft:adventure/root": (1, 0, "冒险起点（0 分）"),
    "minecraft:adventure/kill_a_mob": (2, 1, "杀一只怪"),
    "minecraft:adventure/sleep_in_bed": (2, 1, "睡一觉"),
    "minecraft:adventure/trade": (3, 1, "和村民交易"),
    "minecraft:adventure/avoid_vibration": (4, 1, "潜行躲过监守者"),
    "minecraft:adventure/totem_of_undying": (4, 2, "拿到不死图腾"),
    "minecraft:adventure/sniper_duel": (5, 2, "50 格外狙杀骷髅"),
    "minecraft:adventure/bullseye": (5, 2, "靶心正中"),
    "minecraft:adventure/kill_all_mobs": (5, 2, "杀遍所有敌对生物"),
    "minecraft:adventure/hero_of_the_village": (4, 3, "守村成功"),
    "minecraft:adventure/spyglass_at_parrot": (2, 1, "用望远镜看鹦鹉"),
    "minecraft:adventure/play_jukebox_in_meadows": (3, 2, "在草甸放唱片"),

    # ---------- husbandry ----------
    "minecraft:husbandry/root": (1, 0, "农牧起点（0 分）"),
    "minecraft:husbandry/plant_seed": (2, 1, "种下种子"),
    "minecraft:husbandry/breed_an_animal": (2, 1, "繁殖动物"),
    "minecraft:husbandry/tame_an_animal": (2, 1, "驯服动物"),
    "minecraft:husbandry/fishy_business": (3, 1, "钓一条鱼"),
    "minecraft:husbandry/best_friends_forever": (4, 2, "驯服一只狼"),
    "minecraft:husbandry/balanced_diet": (5, 2, "吃遍所有食物"),
    "minecraft:husbandry/complete_catalogue": (5, 3, "集齐所有猫"),
}


def _guess_depth(adv_id):
    """从 id 猜进度树深度：minecraft:story/mine_stone → 1。"""
    try:
        name = adv_id.split(":", 1)[1]
    except IndexError:
        return 1
    parts = [p for p in name.split("/") if p]
    return max(0, min(6, len(parts) - 1))


def _is_root(adv_id):
    return adv_id.endswith("/root")


def load_table(path):
    """读官方计分表（可选）。支持 dict 或 list 两种写法，坏文件就当没有。"""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}

    table = {}
    items = raw.items() if isinstance(raw, dict) else (
        [(x.get("id"), x) for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
    )
    for adv_id, entry in items:
        if not adv_id or not isinstance(entry, dict):
            continue
        try:
            difficulty = int(entry.get("difficulty", entry.get("level", 2)))
            depth = int(entry.get("depth", _guess_depth(str(adv_id))))
        except (TypeError, ValueError):
            continue
        difficulty = max(1, min(5, difficulty))
        raw_score = entry.get("score")
        if raw_score is None:
            scored = True
            value = BASE_SCORE[difficulty]
        else:
            try:
                value = max(0, int(raw_score))
            except (TypeError, ValueError):
                value = 0
            scored = bool(entry.get("scored", value > 0))
            if not scored:
                value = 0
        table[str(adv_id)] = {
            "difficulty": difficulty,
            "depth": max(0, depth),
            "score": value,
            "scored": scored,
            "hint": str(entry.get("hint") or entry.get("condition") or entry.get("name") or ""),
        }
    return table


def lookup(adv_id, table=None):
    if table and adv_id in table:
        return table[adv_id]
    if adv_id in CURATED:
        difficulty, depth, hint = CURATED[adv_id]
        return {"difficulty": difficulty, "depth": depth, "hint": hint}
    return None


def score(adv_id, frame="task", table=None):
    """算一个 advancement 值多少分（根节点 0 分、不计分项 0 分）。"""
    if not adv_id or _is_root(adv_id):
        return 0
    entry = lookup(adv_id, table)
    if entry:
        if "score" in entry:
            return int(entry["score"])
        return BASE_SCORE.get(int(entry.get("difficulty", 2)), 15)
    return BASE_SCORE.get(FRAME_DIFFICULTY.get((frame or "task").lower(), 2), 15)


def hint_of(adv_id, table=None):
    entry = lookup(adv_id, table)
    if entry and entry.get("hint"):
        return entry["hint"]
    if _is_root(adv_id):
        return "分类起点"
    return ""


def difficulty_of(adv_id, frame="task", table=None):
    entry = lookup(adv_id, table)
    if entry:
        return int(entry["difficulty"])
    return FRAME_DIFFICULTY.get((frame or "task").lower(), 2)


def describe(adv_id, frame="task", table=None):
    """给日志用的一行说明。"""
    return f"{adv_id}（{score(adv_id, frame, table)} 分，难度 {difficulty_of(adv_id, frame, table)}）"
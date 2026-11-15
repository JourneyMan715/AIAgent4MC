# -*- coding: utf-8 -*-
"""
把 RULES.md（项目根或 Doc/ 下）里的计分表解析成 `data/advancement_scores.json`。

什么时候跑：
- 官方更新了计分表（RULES.md 变了）→ 在 Codes 目录执行 `python build_scores_table.py`；
- 平时不用跑，仓库里已经带了一份生成好的（99 项计分 / 3385 分）。
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RULES_CANDIDATES = [
    os.path.join(ROOT, "RULES.md"),
    os.path.join(ROOT, "Doc", "RULES.md"),
]
RULES = next((p for p in RULES_CANDIDATES if os.path.isfile(p)), RULES_CANDIDATES[-1])
OUT = os.path.join(HERE, "data", "advancement_scores.json")

ROW = re.compile(
    r"^\|\s*`(minecraft:[^`]+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|"
)

table = {}
with open(RULES, "r", encoding="utf-8") as f:
    for line in f:
        m = ROW.match(line.strip())
        if not m:
            continue
        adv_id, name, difficulty, score, condition = [x.strip() for x in m.groups()]
        try:
            diff = int(difficulty)
        except ValueError:
            continue
        scored = score.isdigit()
        entry = {
            "difficulty": diff,
            "score": int(score) if scored else 0,
            "scored": scored,
            "name": name,
            "condition": condition,
        }
        table[adv_id] = entry

table["_readme"] = (
    "由 RULES.md（项目根或 Doc/）的计分表（2026-11-13 草案 v2）自动生成。"
    "分值只由难度决定；scored=false 表示不计分。官方更新后重新生成即可。"
)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(table, f, ensure_ascii=False, indent=1, sort_keys=True)

scored = [k for k, v in table.items() if isinstance(v, dict) and v.get("scored")]
total = sum(table[k]["score"] for k in scored)
print(f"共 {len(scored)} 项计分，总分 {total} 分")
print(f"写入 {OUT}")
for key in ("minecraft:story/mine_stone", "minecraft:end/kill_dragon",
            "minecraft:adventure/arbalistic", "minecraft:nether/all_effects"):
    print(key, table.get(key))

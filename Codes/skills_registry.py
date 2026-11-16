# -*- coding: utf-8 -*-
"""可执行技能注册表（JSON）。

`data/skills.json` 里每个条目都是**可执行 spec**：

1. 原子技能（Python 实现，复杂算法仍放 Python）：
   {"name":"chop_tree","type":"python","handler":"chop_tree","args":"...","help":"..."}

2. 组合技能（JSON steps，直接执行）：
   {"name":"bootstrap_wood","type":"macro","steps":[
       {"skill":"chop_tree","args":{}},
       {"skill":"craft","args":{"item":"crafting_table","count":1}}
   ]}

模型只输出 {"skill": name, "args": {...}}；执行器按 JSON spec 分发。
学习层新打包的组合技能在 `state/skills.json`，和内置 JSON 宏合并。
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "data", "skills.json")

# 开箱即用的 JSON 组合技能：模型选一次，执行器连续做完
BUILTIN_MACROS = [
    {
        "name": "bootstrap_wood",
        "type": "macro",
        "description": "砍树 → 工作台 → 木镐（新手基础盘）",
        "args": {},
        "steps": [
            {"skill": "chop_tree", "args": {"max_logs": 6}},
            {"skill": "craft", "args": {"item": "crafting_table", "count": 1}},
            {"skill": "craft", "args": {"item": "wooden_pickaxe", "count": 1}},
        ],
    },
    {
        "name": "bootstrap_stone",
        "type": "macro",
        "description": "木镐 → 挖石头 → 石镐",
        "args": {},
        "steps": [
            {"skill": "craft", "args": {"item": "wooden_pickaxe", "count": 1}},
            {"skill": "collect", "args": {"group": "stone", "count": 8, "limit": 8}},
            {"skill": "craft", "args": {"item": "stone_pickaxe", "count": 1}},
        ],
    },
]


def build_registry():
    from skills import SKILLS

    skills = []
    for name, spec in SKILLS.items():
        skills.append({
            "name": name,
            "type": "python",
            "handler": name,
            "executable": True,
            "args": spec.get("args", ""),
            "help": spec.get("help", ""),
        })
    skills.extend(BUILTIN_MACROS)
    return {"version": 2, "skills": skills}


def save(path=None):
    path = path or DEFAULT_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_registry(), fh, ensure_ascii=False, indent=2)
    return path


def load(path=None):
    path = path or DEFAULT_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("skills"), list):
            return data
    except (OSError, ValueError):
        pass
    return build_registry()


def _learned():
    try:
        from dynamic_skills import DynamicSkills
        return DynamicSkills().specs()
    except Exception:
        return []


def merged(path=None):
    """静态 JSON + 学习到的 state/skills.json（同名时学习版覆盖）。"""
    data = load(path)
    result = {s["name"]: s for s in data.get("skills") or [] if isinstance(s, dict) and s.get("name")}
    for spec in _learned():
        name = spec.get("name")
        if name:
            spec = dict(spec)
            spec.setdefault("type", "macro")
            result[name] = spec
    return list(result.values())


def has_skill(name, path=None):
    return any(s.get("name") == name for s in merged(path))


def find(name, path=None):
    for spec in merged(path):
        if spec.get("name") == name:
            return spec
    return None


def help_text(only=None, extra=None):
    lines = []
    for item in merged():
        name = item.get("name")
        if only and name not in only:
            continue
        kind = item.get("type", "python")
        suffix = "（组合）" if kind == "macro" else ""
        desc = item.get("help") or item.get("description") or ""
        lines.append(f"- {name}{suffix}({item.get('args', '')})：{desc}")
    if extra:
        lines.append("")
        lines.append("【学习到的组合技能（JSON macro）】")
        lines.append(extra)
    return "\n".join(lines)


def _subst(value, args):
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        key = value[1:-1]
        return args.get(key, value)
    if isinstance(value, dict):
        return {k: _subst(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, args) for v in value]
    return value


def execute(api, state, name, args=None, depth=0):
    """按 JSON spec 执行；macro 可嵌套，python 技能交给 skills.run_skill。"""
    from skills import run_skill

    if depth > 5:
        raise RuntimeError(f"组合技能嵌套太深：{name}")
    spec = find(name)
    if not spec:
        raise RuntimeError(f"没有这个技能：{name}")
    kind = spec.get("type", "python")
    call_args = dict(spec.get("args") if isinstance(spec.get("args"), dict) else {})
    call_args.update(args or {})

    if kind == "python":
        return run_skill(api, state, spec.get("handler") or name, call_args)

    if kind == "macro":
        steps = spec.get("steps") or []
        if not steps:
            raise RuntimeError(f"组合技能 {name} 没有 steps")
        summaries = []
        for step in steps:
            skill = str(step.get("skill") or "")
            step_args = _subst(step.get("args") or {}, call_args)
            result = execute(api, state, skill, step_args, depth + 1)
            summaries.append(str((result or {}).get("summary") or skill))
        return {
            "summary": f"组合 {name}：" + " → ".join(summaries),
            "skill": name,
            "steps": len(summaries),
        }

    raise RuntimeError(f"未知技能类型：{kind}")

# -*- coding: utf-8 -*-
"""学习成果打包成的 JSON 组合技能（macro skill）。

一个 skill 就是一段 JSON：
{
  "name": "make_wooden_pickaxe",
  "description": "做木镐",
  "args": {"count": 1},
  "steps": [
    {"skill": "chop_tree", "args": {}},
    {"skill": "craft", "args": {"item": "wooden_pickaxe", "count": 1}}
  ]
}

模型只输出 {"skill": name, "args": {...}}；执行器按 steps 调已有 Python 技能。
这样模型不需要每一步都重新决定底层动作。

文件：`Codes/state/skills.json`（运行时数据，已 gitignore）。
"""

import json
import os
import time

from skills import SKILLS, SkillError, run_skill

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "state", "skills.json")
MAX_SKILLS = 100
MAX_STEPS = 30


class DynamicSkills:
    def __init__(self, path=None):
        self.path = path or DEFAULT_PATH
        self.data = {"version": 1, "skills": []}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError):
            return self.data
        if isinstance(loaded, dict) and isinstance(loaded.get("skills"), list):
            self.data = {
                "version": 1,
                "skills": [s for s in loaded["skills"] if isinstance(s, dict)],
            }
        return self.data

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def specs(self):
        return self.data.get("skills") or []

    def has(self, name):
        return any(s.get("name") == name for s in self.specs())

    def get(self, name):
        for spec in self.specs():
            if spec.get("name") == name:
                return spec
        return None

    def add(self, name, description, steps, args=None, notes=None):
        name = str(name or "").strip()[:80]
        if not name:
            raise SkillError("bad_args", "组合技能需要 name")
        if not isinstance(steps, list) or not steps:
            raise SkillError("bad_args", "组合技能需要非空 steps")
        clean_steps = []
        for step in steps[:MAX_STEPS]:
            if not isinstance(step, dict):
                continue
            skill = str(step.get("skill") or "").strip()
            if not skill:
                continue
            clean_steps.append({
                "skill": skill,
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
            })
        if not clean_steps:
            raise SkillError("bad_args", "steps 里没有有效技能")
        spec = {
            "name": name,
            "description": str(description or notes or "")[:300],
            "args": args if isinstance(args, dict) else {},
            "steps": clean_steps,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data["skills"] = [s for s in self.specs() if s.get("name") != name]
        self.data["skills"].append(spec)
        del self.data["skills"][:-MAX_SKILLS]
        self.save()
        return spec

    def summary_lines(self, limit=20):
        lines = []
        for spec in self.specs()[-limit:]:
            steps = " → ".join(s.get("skill", "?") for s in spec.get("steps") or [])
            lines.append(f"- {spec.get('name')}：{spec.get('description') or ''}（{steps}）")
        return "\n".join(lines)

    def as_json(self):
        return json.dumps(self.data, ensure_ascii=False, indent=2)


def _subst(value, args):
    """把 "{{name}}" / "{name}" 形式的占位符替换成调用参数。"""
    if isinstance(value, str):
        if value.startswith("{") and value.endswith("}"):
            key = value[1:-1]
            if key in args:
                return args[key]
        return value
    if isinstance(value, dict):
        return {k: _subst(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, args) for v in value]
    return value


def run_dynamic(api, state, name, args=None, depth=0, registry=None):
    """执行 JSON 组合技能；steps 可以是 Python 技能，也可以是另一个组合技能。"""
    if depth > 5:
        raise SkillError("skill_too_deep", f"组合技能嵌套太深：{name}")
    registry = registry or DynamicSkills()
    spec = registry.get(name)
    if not spec:
        raise SkillError("unknown_skill", f"没有组合技能：{name}")

    call_args = dict(spec.get("args") or {})
    call_args.update(args or {})
    summaries = []
    for step in spec.get("steps") or []:
        skill = str(step.get("skill") or "")
        step_args = _subst(step.get("args") or {}, call_args)
        if registry.has(skill):
            result = run_dynamic(api, state, skill, step_args, depth + 1, registry)
        elif skill in SKILLS:
            result = run_skill(api, state, skill, step_args)
        else:
            raise SkillError("unknown_skill", f"组合技能 {name} 里有未知步骤：{skill}")
        summaries.append(str((result or {}).get("summary") or skill))
    return {
        "summary": f"组合 {name}：" + " → ".join(summaries),
        "skill": name,
        "steps": len(summaries),
    }

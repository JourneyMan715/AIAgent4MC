# -*- coding: utf-8 -*-
"""生成 `data/skills.json`（技能注册表）。改了 skills.py 的技能表后跑一次。"""

import os

import skills_registry

if __name__ == "__main__":
    path = skills_registry.save()
    print(f"写入 {path}（{len(skills_registry.load()['skills'])} 个技能）")
    print(os.path.relpath(path))

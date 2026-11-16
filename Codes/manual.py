# -*- coding: utf-8 -*-
"""
学习层的手动动作测试（不调用大模型）。

用途：赛前/调技能时，直接输入技能或原始 API 调用，立刻看结果，并记录到 memory。

启动：
    python main.py learn --manual

命令：
    skills                         列出可用技能
    status                         看 Bot 状态
    inv                            看背包
    do <skill> [json]              跑技能，如 do scan {} / do craft {"item":"oak_planks","count":4}
    call <method> [json]           调原始 API，如 call dig {"pos":[1,64,2],"collect":true}
    help                           帮助
    exit / quit                    退出

说明：
- `do` 只允许安全技能（与学习层一致）；
- `call` 禁止 login/logout/connect/disconnect，避免把会话弄乱；
- 每次 `do` 会写 `state/knowledge.json`，供决策层使用；
- 这个模式不花 token、不需要模型 key。
"""

import json
import os
import time

from knowledge import KnowledgeBase
from loopguard import LoopGuard
from mc_api import McApiError
from skills import SKILLS, SkillError
from skills_registry import execute as execute_skill, has_skill
from trajectory import Trajectory
from world_state import WorldState

_HERE = os.path.dirname(os.path.abspath(__file__))

SAFE_SKILLS = list(SKILLS.keys())

CALLABLE = [
    "status", "inventory", "find_blocks", "get_block", "get_entities",
    "events", "advancements", "move_to", "dig", "place", "interact",
    "use_item", "attack", "shoot", "craft", "window_view", "window_act",
    "equip", "posture", "dismount", "drop", "chat", "stop",
]


class ManualTester:
    def __init__(self, api, state, knowledge=None, *, input_fn=None, traj=None,
                 control=None):
        self.api = api
        self.state = state
        self.knowledge = knowledge or KnowledgeBase()
        self.input_fn = input_fn or input
        self.control = control
        self.loop_guard = LoopGuard(10)
        self.traj = traj or Trajectory(
            os.path.join(_HERE, "logs"),
            run_id="manual-" + time.strftime("%Y%m%d-%H%M%S"))
        self.stop = False

    def run(self):
        print("=" * 58)
        print("🛠 手动动作测试（学习层）—— 不调用模型、不花 token")
        print("   输入 help 看命令；exit 退出")
        print(f"   可用技能：{', '.join(SAFE_SKILLS)}")
        print("=" * 58)
        try:
            while not self.stop:
                if self.control:
                    if self.control.stop:
                        break
                    if self.control.paused:
                        time.sleep(0.2)
                        continue
                try:
                    if self.control:
                        self.control.suspend()
                    line = self.input_fn("动作> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                finally:
                    if self.control:
                        self.control.resume()
                if line:
                    self.handle(line)
        finally:
            self.knowledge.export_markdown()
            self.traj.log("manual_finish", knowledge=self.knowledge.counts())
            self.traj.close()
            self.state.save()
            print(f"\n🧠 memory：{self.knowledge.counts()}")

    def handle(self, line):
        """执行一条命令；返回 True 表示继续，False 表示退出。"""
        parts = line.strip().split(maxsplit=2)
        if not parts:
            return True
        cmd = parts[0].lower()

        if cmd == "q" and self.control:
            self.control.toggle_pause()
            return True
        if cmd == "e" and self.control:
            self.control.emergency()
            return True
        if self.loop_guard.hit(line.strip().lower()):
            print(f"  🔁 连续 {self.loop_guard.limit} 轮相同命令，判定死循环，停止")
            self.stop = True
            return False
        if cmd in ("exit", "quit", "退出"):
            self.stop = True
            return False
        if cmd in ("help", "?"):
            self._help()
            return True
        if cmd in ("skills", "ls"):
            print("可用技能：")
            for name in SAFE_SKILLS:
                print(f"  - {name}")
            return True
        if cmd in ("status", "st"):
            self._status()
            return True
        if cmd in ("inv", "inventory"):
            self._inventory()
            return True
        if cmd == "do":
            self._do(parts)
            return True
        if cmd == "call":
            self._call(parts)
            return True
        print(f"未知命令：{cmd}（输入 help）")
        return True

    # ---------------- 命令实现 ----------------
    def _help(self):
        print(
            "  skills                         列出可用技能\n"
            "  status                         看状态\n"
            "  inv                            看背包\n"
            "  do <skill> [json]              跑技能（全部技能 + 学习生成的组合技能）\n"
            "  call <method> [json]           调原始 API（禁止 login/logout/connect/disconnect）\n"
            "  exit                           退出"
        )

    def _status(self):
        try:
            data = self.api.status() or {}
        except McApiError as e:
            print(f"❌ {e.code}: {e.message}")
            return
        keep = {k: data.get(k) for k in
                ("pos", "health", "food", "dimension", "is_day", "biome",
                 "in_vehicle", "nearest_hostile") if k in data}
        print(json.dumps(keep, ensure_ascii=False, indent=2))
        self.state.update_status(data)
        self.state.save()

    def _inventory(self):
        try:
            inv = self.api.inventory() or {}
        except McApiError as e:
            print(f"❌ {e.code}: {e.message}")
            return
        items = inv.get("items") or []
        if not items:
            print("（空）")
            return
        print("、".join(f"{it.get('name')}×{it.get('count')}" for it in items))

    def _do(self, parts):
        if len(parts) < 2:
            print("用法：do <skill> [json]，例如 do scan {}")
            return
        skill = parts[1].strip().lower()
        if skill not in SAFE_SKILLS and not has_skill(skill):
            print(f"❌ 未知技能：{skill}（可用：{', '.join(SAFE_SKILLS)}；或学习生成的组合技能）")
            return
        args = self._parse_args(parts[2] if len(parts) > 2 else "{}")
        if args is None:
            return
        before = dict(self.state.inventory())
        started = time.time()
        try:
            result = execute_skill(self.api, self.state, skill, args) or {}
            summary = str(result.get("summary") or skill)
            print(f"✅ {summary}（{time.time() - started:.0f}s）")
            ok = True
        except (SkillError, McApiError) as e:
            summary = f"{getattr(e, 'code', 'error')}: {getattr(e, 'message', str(e))}"
            print(f"❌ {summary}")
            ok = False
        except Exception as e:
            summary = f"{type(e).__name__}: {e}"
            print(f"❌ 意外错误：{summary}")
            ok = False

        after = dict(self.state.inventory())
        delta = {}
        for name in set(before) | set(after):
            diff = int(after.get(name, 0)) - int(before.get(name, 0))
            if diff:
                delta[name] = diff
        self.knowledge.add_experiment(skill, args, ok, summary, delta)
        self.state.add_recent(skill, ok, summary)
        self.state.save()
        self.traj.log("manual_do", skill=skill, args=args, ok=ok,
                      summary=summary, delta=delta)
        if delta:
            print("   背包变化：" + "、".join(f"{k}{v:+d}" for k, v in delta.items()))

    def _call(self, parts):
        if len(parts) < 2:
            print("用法：call <method> [json]，例如 call status {}")
            return
        method = parts[1].strip()
        if method not in CALLABLE:
            print(f"❌ 不允许的方法；可用：{', '.join(CALLABLE)}")
            return
        kwargs = self._parse_args(parts[2] if len(parts) > 2 else "{}")
        if kwargs is None:
            return
        try:
            result = getattr(self.api, method)(**kwargs)
            print(json.dumps(result, ensure_ascii=False)[:3000])
            self.traj.log("manual_call", method=method, args=kwargs, ok=True)
        except McApiError as e:
            print(f"❌ {e.code}: {e.message}")
            self.traj.log("manual_call", method=method, args=kwargs,
                          ok=False, code=e.code, message=e.message)
        except TypeError as e:
            print(f"❌ 参数不对：{e}")
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")

    def _parse_args(self, text):
        try:
            data = json.loads(text or "{}")
        except ValueError as e:
            print(f"❌ JSON 解析失败：{e}")
            return None
        if not isinstance(data, dict):
            print("❌ 参数必须是 JSON 对象，如 {\"item\":\"oak_planks\"}")
            return None
        return data

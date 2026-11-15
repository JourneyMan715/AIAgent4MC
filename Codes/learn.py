# -*- coding: utf-8 -*-
"""
学习层（LearnAgent）—— 比赛前把“游戏规则”学明白，不冲分。

它和决策层的区别：
- 决策层：读用户写的 strategy，按 advancement 分数做最优选择；
- 学习层：做实验、问用户、把结论写进 memory（knowledge.json/md）。

学习层的动作只有四类：
1. `ask_user`：有疑问直接问用户（终端交互），把回答记进 memory；
2. `experiment`：调用一个技能做实验，记录结果与背包变化；
3. `record_rule` / `record_skill`：把结论写进 memory；
4. `scan` / `survey` / `done`。

运行：
    python main.py learn --minutes 20
    python main.py learn --ticks 50
    python main.py learn --reset-knowledge   # 清空旧记忆，从零学

注意：
- 学习层会**阻塞等用户输入**，适合赛前 pretraining，不适合无人值守比赛；
- 它不读 strategy，也不追 advancement；目标是把规则/技能沉淀到 memory。
"""

import json
import os
import time

import config
from knowledge import KnowledgeBase
from llm import BudgetExceeded, LLMFatalError, LLMError
from loopguard import LoopGuard, action_signature
from mc_api import McApiError
from skills import SKILLS, SkillError, run_skill, skill_help
from trajectory import Trajectory
from world_state import WorldState

_HERE = os.path.dirname(os.path.abspath(__file__))

# 学习阶段允许的实验技能：先不碰战斗/下界/末地，避免把测试世界搞崩
LEARN_SKILLS = [
    "survey", "scan", "chop_tree", "collect", "craft",
    "smelt", "eat", "equip", "place", "explore",
]

SYSTEM_PROMPT = """你是 Minecraft 的学习层（不是比赛执行层，也不是聊天助手）。
你的目标：通过**做实验 + 问用户**，把游戏规则、合成配方、掉落、工具、生物行为、接口行为学清楚，写进 memory。
现在**不要追求 advancement 分数**，也不要执行比赛策略；只有把规则学明白，之后才能拿分。

可用动作（每轮只输出一个 JSON，不要代码块、不要多余文字）：
{{"action":"ask_user","question":"一个具体问题","why":"为什么需要问"}}
{{"action":"experiment","skill":"技能名","args":{{...}},"topic":"想验证什么","hypothesis":"你的假设"}}
{{"action":"record_rule","topic":"规则分类","rule":"结论","evidence":"来自哪个实验/用户回答"}}
{{"action":"record_skill","name":"技能名","notes":"什么时候用、注意什么","args":{{...}}}}
{{"action":"scan"}}
{{"action":"survey"}}
{{"action":"done","why":"学够了/到时间了"}}

可用技能：
{skills}

学习原则：
1. **先问后做**：开始学习前先用 ask_user 问清目标/偏好；`must_ask=true` 时禁止先做实验。
2. 先读 `events`：受伤、死亡、聊天、进度变化都是情境；先解释/提问，再决定实验，不要无视事件。
3. 先看 memory 里已经有什么，优先补**还不知道**的规则，不要重复已确认的结论。
4. 不确定、有歧义、需要用户偏好（比如“先学哪条线”）→ ask_user；一次只问一个具体问题，不要一次问一串。
5. 做实验前给假设；实验后用 record_rule 记录结论。结论要具体（“橡木原木→4 木板”比“可以合成”好）。
6. **情境要连贯**：memory 的 episode 是连续学习过程；下一轮实验要延续上一轮（改参数/换条件/验证结论），不要每次从零开始。
7. 不要连续重复同一个实验；失败也是结论，记录失败条件和错误码。
8. 只在安全范围实验（不主动下矿洞深处、不惹怪、不去下界/末地）。
9. 不要冲 advancement，不要为了分数冒险。
10. 用户回答后，要基于回答继续，不要重复问同一个问题。
11. 只输出 JSON，字段用双引号。
"""


class LearnAgent:
    def __init__(self, api, llm, state, knowledge=None, *, input_fn=None,
                 traj=None, cfg=None, control=None):
        self.api = api
        self.llm = llm
        self.state = state
        self.knowledge = knowledge or KnowledgeBase()
        self.input_fn = input_fn or input
        self.cfg = cfg or config
        self.control = control
        self.traj = traj or Trajectory(os.path.join(_HERE, "logs"),
                                       run_id="learn-" + time.strftime("%Y%m%d-%H%M%S"))
        self.stop = False
        self.stop_reason = ""
        self.ticks = 0
        self.last_experiment = None
        self.last_answer = None
        self.last_question = None
        self.recent_events = []
        self.episode = None
        self.must_ask = True
        self.loop_guard = LoopGuard(getattr(self.cfg, "LOOP_LIMIT", 10))

    # ================= 主循环 =================
    def run(self, deadline=None, max_ticks=200):
        self.deadline = deadline
        print("=" * 58)
        print("🧠 学习层启动：目标是把游戏规则学明白，不冲分")
        print("   有疑问会直接问你；输入 exit / quit 可结束")
        print(f"   已有 memory：{self.knowledge.counts()}")
        print("=" * 58)
        self.traj.log("learn_start", knowledge=self.knowledge.counts())
        try:
            while not self.stop:
                if self.control:
                    if self.control.stop:
                        self.stop_reason = self.control.reason or "用户强制下线"
                        break
                    if self.control.paused:
                        time.sleep(0.2)
                        continue
                if deadline and time.time() >= deadline:
                    self.stop_reason = "到时间了"
                    break
                if self.ticks >= int(max_ticks or 200):
                    self.stop_reason = "达到最大学习轮数"
                    break
                self.ticks += 1
                print(f"\n—— 学习轮 {self.ticks} ——")
                try:
                    self._observe()
                    if not self.episode:
                        self.episode = self.knowledge.start_episode(self._context())
                        self.traj.log("learn_episode", id=self.episode.get("id"))
                    decision = self._decide()
                    payload = {key: decision.get(key) for key in
                               ("skill", "args", "question", "topic", "rule", "name", "notes")}
                    signature = action_signature(decision.get("action"), payload, self.state)
                    if self.loop_guard.hit(signature):
                        print(f"  🔁 连续 {self.loop_guard.limit} 轮相同动作/上下文，判定死循环，自动停止")
                        self.traj.log("learn_loop_stop", signature=signature)
                        self.stop_reason = f"连续 {self.loop_guard.limit} 轮死循环"
                        break
                    self._execute(decision)
                except BudgetExceeded as e:
                    self.stop_reason = f"token 预算用完：{e}"
                    break
                except LLMFatalError as e:
                    self.stop_reason = f"模型不可用：{e}"
                    break
                except (LLMError, ValueError) as e:
                    print(f"  ⚠️ 学习决策失败：{e}")
                    self.traj.log("learn_error", error=str(e))
                    time.sleep(2)
                except McApiError as e:
                    print(f"  ⚠️ 接口错误：{e.code} {e.message}")
                    self.traj.log("learn_api_error", code=e.code, message=e.message)
                    time.sleep(2)
        except KeyboardInterrupt:
            print("\n⏹ 收到 Ctrl+C，结束学习")
            self.stop_reason = "用户中断"
        finally:
            self._finish()

    # ================= 观察 / 提示词 =================
    def _observe(self):
        status = self.api.status() or {}
        self.state.update_status(status)
        self._refresh_inventory()
        try:
            events = self.api.events() or {}
            raw = events.get("events") or []
            self.recent_events = [e for e in raw if isinstance(e, dict)][-8:]
            for event in self.recent_events:
                self.state.add_event(self._event_text(event))
        except McApiError:
            pass
        self.state.save()

    @staticmethod
    def _event_text(event):
        kind = str(event.get("type") or "?")
        if kind == "chat":
            return "聊天：" + str(event.get("text") or event.get("message") or "")
        if kind == "hurt":
            attacker = event.get("likely_attacker") or {}
            name = attacker.get("name") if isinstance(attacker, dict) else ""
            return f"受伤 -{event.get('damage')} {name or ''}".strip()
        if kind == "death":
            return "死亡"
        return f"{kind}：{event.get('text') or event.get('message') or ''}".strip()

    def _context(self):
        inv = self.state.inventory()
        top = sorted(inv.items(), key=lambda kv: -int(kv[1]))[:10]
        return {
            "pos": self.state.data.get("pos"),
            "dimension": self.state.data.get("dimension"),
            "health": self.state.data.get("health"),
            "food": self.state.data.get("food"),
            "inventory": {k: v for k, v in top},
            "events": [self._event_text(e) for e in self.recent_events[-4:]],
        }

    def _refresh_inventory(self):
        inv = self.api.inventory() or {}
        items = inv.get("items") or []
        self.state.data["inventory"] = {
            str(it.get("name")): int(it.get("count") or 0)
            for it in items if isinstance(it, dict) and it.get("name")
        }
        self.state.data["inventory_at"] = time.time()

    def _brief(self, time_left_min=None):
        inv = self.state.inventory()
        top = sorted(inv.items(), key=lambda kv: -int(kv[1]))[:16]
        return {
            "pos": self.state.data.get("pos"),
            "health": self.state.data.get("health"),
            "food": self.state.data.get("food"),
            "dimension": self.state.data.get("dimension"),
            "inventory": {k: v for k, v in top},
            "events": [self._event_text(e) for e in self.recent_events[-6:]],
            "memory": self.knowledge.summary(max_rules=25, max_skills=10, max_qa=8),
            "episode": {
                "id": (self.episode or {}).get("id"),
                "context": (self.episode or {}).get("context"),
                "turns": ((self.episode or {}).get("turns") or [])[-4:],
            },
            "must_ask": self.must_ask,
            "last_experiment": self.last_experiment,
            "last_answer": self.last_answer,
            "time_left_min": round(time_left_min) if time_left_min is not None else None,
        }

    def _decide(self):
        time_left = None
        if getattr(self, "deadline", None):
            time_left = max(0.0, (self.deadline - time.time()) / 60.0)
        system = SYSTEM_PROMPT.format(skills=skill_help(LEARN_SKILLS))
        user = json.dumps(self._brief(time_left), ensure_ascii=False,
                          separators=(",", ":"))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        obj = self.llm.chat_json(messages)
        decision = self._validate(obj)
        if decision is None:
            print("  ⚠️ 模型输出不合法，这轮先 scan")
            decision = {"action": "scan"}
        if self.must_ask and decision.get("action") not in ("ask_user", "done"):
            print("  🔎 先问再动手：这一轮先向用户确认")
            decision = {
                "action": "ask_user",
                "question": "开始前先确认：这轮你想让我重点学什么？",
                "why": "先问后做",
            }
        return decision

    def _validate(self, obj):
        if not isinstance(obj, dict):
            return None
        action = str(obj.get("action") or "").strip().lower()
        if action in ("done", "stop", "finish", "complete", "task_complete") or obj.get("done") is True:
            return {"action": "done", "why": str(obj.get("why") or "")[:120]}
        if action == "ask_user":
            question = str(obj.get("question") or "").strip()
            if not question:
                return None
            return {"action": "ask_user", "question": question[:500],
                    "why": str(obj.get("why") or "")[:120]}
        if action == "record_rule":
            topic = str(obj.get("topic") or "未分类")[:120]
            rule = str(obj.get("rule") or "").strip()
            if not rule:
                return None
            return {"action": "record_rule", "topic": topic, "rule": rule[:1500],
                    "evidence": str(obj.get("evidence") or "")[:300]}
        if action == "record_skill":
            name = str(obj.get("name") or "").strip()
            notes = str(obj.get("notes") or "").strip()
            if not name or not notes:
                return None
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            return {"action": "record_skill", "name": name[:80],
                    "notes": notes[:800], "args": args}
        if action in ("scan", "survey"):
            return {"action": action}
        if action == "experiment":
            skill = str(obj.get("skill") or "").strip().lower()
            if skill not in LEARN_SKILLS:
                return None
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            return {
                "action": "experiment",
                "skill": skill,
                "args": args,
                "topic": str(obj.get("topic") or "")[:120],
                "hypothesis": str(obj.get("hypothesis") or "")[:300],
            }
        return None

    # ================= 执行 =================
    def _turn(self, turn):
        if self.episode:
            self.knowledge.add_turn(self.episode["id"], turn)

    def _execute(self, decision):
        action = decision["action"]
        if action == "done":
            self.stop = True
            self.stop_reason = decision.get("why") or "模型认为学够了"
            return
        if action == "ask_user":
            self._ask(decision)
            return
        if action == "record_rule":
            item = self.knowledge.add_rule(
                decision["topic"], decision["rule"], source="llm",
                evidence=decision.get("evidence"))
            if item:
                print(f"  📝 记下规则 [{item['topic']}]：{item['rule']}")
                self._turn({"kind": "rule", "topic": item["topic"], "rule": item["rule"]})
                self.traj.log("learn_rule", topic=item["topic"], rule=item["rule"])
            return
        if action == "record_skill":
            item = self.knowledge.add_skill(
                decision["name"], decision["notes"], decision.get("args"))
            if item:
                print(f"  📝 记下技能：{item['name']}")
                self._turn({"kind": "skill", "text": item["name"] + "：" + item["notes"]})
                self.traj.log("learn_skill", name=item["name"])
            return
        if action in ("scan", "survey"):
            self._experiment(action, {}, topic=action, hypothesis="")
            return
        if action == "experiment":
            self._experiment(
                decision["skill"], decision.get("args") or {},
                topic=decision.get("topic"), hypothesis=decision.get("hypothesis"))
            return

    def _experiment(self, skill, args, topic=None, hypothesis=None):
        print(f"  🧪 实验：{skill}({json.dumps(args, ensure_ascii=False)})")
        if hypothesis:
            print(f"     假设：{hypothesis}")
        before = dict(self.state.inventory())
        started = time.time()
        ok = False
        summary = ""
        try:
            result = run_skill(self.api, self.state, skill, args) or {}
            summary = str(result.get("summary") or skill)
            ok = True
            print(f"  ✅ {summary}（{time.time() - started:.0f}s）")
        except (SkillError, McApiError) as e:
            summary = f"{getattr(e, 'code', 'error')}: {getattr(e, 'message', str(e))}"
            print(f"  ❌ {summary}")
        except Exception as e:  # 学习层不能被单个实验打断
            summary = f"{type(e).__name__}: {e}"
            print(f"  ❌ 意外错误：{summary}")

        try:
            self._refresh_inventory()
        except McApiError:
            pass
        after = dict(self.state.inventory())
        delta = {}
        for name in set(before) | set(after):
            diff = int(after.get(name, 0)) - int(before.get(name, 0))
            if diff:
                delta[name] = diff
        self.last_experiment = {
            "skill": skill, "args": args, "ok": ok,
            "summary": summary, "delta": delta,
            "topic": topic, "hypothesis": hypothesis,
        }
        self.knowledge.add_experiment(
            skill, args, ok, summary, delta,
            context=self._context(),
            episode_id=(self.episode or {}).get("id"))
        self._turn({
            "kind": "experiment",
            "skill": skill,
            "args": args,
            "ok": ok,
            "summary": summary,
        })
        self.state.add_recent(skill, ok, summary)
        self.state.save()
        self.traj.log("learn_experiment", skill=skill, args=args, ok=ok,
                      summary=summary, delta=delta, topic=topic, hypothesis=hypothesis)

    def _ask(self, decision):
        question = decision["question"]
        self.last_question = question
        print(f"\n❓ 学习层问你：{question}")
        if decision.get("why"):
            print(f"   （为什么问：{decision['why']}）")
        if self.control:
            self.control.suspend()
        try:
            answer = self.input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            self.stop = True
            self.stop_reason = "用户结束问答"
            return
        finally:
            if self.control:
                self.control.resume()
        if answer.lower() in ("exit", "quit", "退出", "结束"):
            self.stop = True
            self.stop_reason = "用户输入退出"
            return
        if answer.lower() == "/e" and self.control:
            self.control.emergency()
            return
        if not answer:
            answer = "（用户没有回答）"
        self.last_answer = {"question": question, "answer": answer}
        self.knowledge.add_qa(question, answer, episode_id=(self.episode or {}).get("id"))
        self._turn({"kind": "ask", "question": question, "answer": answer})
        self.must_ask = False
        self.traj.log("learn_ask", question=question, answer=answer)
        print("  ✅ 已记入 memory")

    # ================= 收尾 =================
    def _finish(self):
        if self.episode:
            lesson = ""
            if self.last_experiment:
                lesson = str(self.last_experiment.get("summary") or "")
            self.knowledge.close_episode(self.episode.get("id"), lesson)
        path = self.knowledge.export_markdown()
        print("\n" + "=" * 58)
        print(f"🧠 学习结束：{self.stop_reason or '完成'}")
        print(f"   本次轮数：{self.ticks}")
        print(f"   memory：{self.knowledge.counts()}")
        print(f"   人类可读：{path}")
        print(f"   轨迹：{self.traj.path}")
        print(f"   🪙 {self.llm.usage_text()}")
        print("=" * 58)
        self.traj.log("learn_finish", reason=self.stop_reason, ticks=self.ticks,
                      knowledge=self.knowledge.counts(), usage=self.llm.usage)
        self.traj.close()
        self.state.save()

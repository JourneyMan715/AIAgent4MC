# -*- coding: utf-8 -*-
"""
主循环（Agent）—— 真正「跑比赛」的那一层。

一圈（tick）做四件事：
    观察（/status /inventory /events /advancements）
      → 保命检查（吃 / 逃 / 死亡循环避让）
      → 问大脑（一次，只问「下一个技能」）
      → 执行技能（几十个底层动作）+ 记账

特色：
- **无人值守**：不再有 input()，目标由 advancement 驱动；
- **token 友好**：每圈只发一小段结构化状态，不重发历史；
- **可恢复**：状态实时落盘，进程崩了 supervisor 拉起来接着跑；
- **可复盘**：每一步都写进 logs/run-*.jsonl。
"""

import json
import os
import time

import config as default_config
import skills
from advancements import load_table, score
from llm import BudgetExceeded, LLMError, LLMFatalError
from loopguard import LoopGuard, action_signature
from mc_api import McApiError
from netcheck import LatencyStats
from planner import Planner
from trajectory import Trajectory, write_task_log

_HERE = os.path.dirname(os.path.abspath(__file__))

# 这些错误是临时/环境问题，重试就好，**不算技能失败、不拉黑**
_TRANSIENT_CODES = {
    "network_error", "internal", "timeout", "busy", "not_connected",
    "wrong_server", "dead", "death_loop_cooldown", "interrupted",
    "http_error", "not_ready",
}

# 观察类技能不拉黑（它们不改变世界，失败重试即可；空转由「无进展检测」处理）
_OBSERVE_SKILLS = {"scan", "survey"}


class Agent:
    def __init__(self, api, llm, state, *, cfg=default_config, planner=None,
                 trajectory=None, verbose=True, decide_locally=False, control=None,
                 autonomous=False, competition=True):
        self.api = api
        self.llm = llm
        self.state = state
        self.cfg = cfg
        self.verbose = verbose
        self.decide_locally = decide_locally
        self.control = control
        self.autonomous = autonomous
        self.competition = competition

        table_path = os.path.join(_HERE, "data", "advancement_scores.json")
        self.table = load_table(table_path)
        self.scorer = lambda adv_id, frame="task": score(adv_id, frame, self.table)
        strategy_path = os.path.join(
            _HERE, getattr(cfg, "STRATEGY_FILE", "data/strategy.json"))
        self.planner = planner or Planner(
            llm, strategy_path=strategy_path,
            autonomous=self.autonomous, competition=self.competition)
        self.traj = trajectory or Trajectory(
            os.path.join(_HERE, cfg.LOG_DIR), state.data.get("run_id"))
        self.llm.on_usage = self.state.update_tokens

        self.stop = False
        self.stop_reason = ""
        self.deadline = None
        self._stop_at = None
        self._last_status = {}
        self._llm_errors = 0
        self._last_decision_key = None
        self._same_decision = 0
        self._progress_sig = None
        self._stagnant = 0
        self.status_stats = LatencyStats()
        self.llm_stats = LatencyStats()
        self.api_call_stats = LatencyStats()
        self._last_net_warn = 0.0
        self._skill_active = False
        self._skill_api_calls = 0
        self._skill_api_ms = 0.0
        self._last_hurt_at = 0.0
        self._last_hurt_by = None
        self._last_hurt_is_player = False
        self._last_hurt_damage = 0.0
        self._last_flee_at = 0.0
        self.loop_guard = LoopGuard(getattr(self.cfg, "LOOP_LIMIT", 10))
        if hasattr(self.api, "on_request"):
            self.api.on_request = self._on_api_request
        if hasattr(self.llm, "on_call"):
            self.llm.on_call = self._on_llm_call
        self.ticks = 0
        self._last_adv = 0.0
        self._last_decision = 0.0
        self.new_advancements = []
        self._started_ids = set(state.completed_ids())
        self._started_at = float(state.data.get("started_at") or time.time())

    # ================================================================
    # 对外入口
    # ================================================================
    def run(self, deadline=None, max_ticks=None):
        self.deadline = deadline
        # 留一点时间做收尾（拉最终进度、写记录）；短时间联调时别留太多
        if deadline:
            margin = 60 if deadline - time.time() > 180 else 5
            self._stop_at = deadline - margin
        else:
            self._stop_at = None
        self._banner(deadline)

        try:
            while not self._should_stop(max_ticks):
                if self.control:
                    if self.control.stop:
                        self.stop_reason = self.control.reason or "用户强制下线"
                        break
                    if self.control.paused:
                        time.sleep(0.2)
                        continue
                self.tick()
        except KeyboardInterrupt:
            self.stop_reason = "手动中断（Ctrl+C）"
        except BudgetExceeded as e:
            self.stop_reason = f"token 预算用尽（{e}）"
        except Exception as e:  # 兜底：不让程序带着状态丢失崩掉
            import traceback
            traceback.print_exc()
            self.stop_reason = f"未处理异常：{e!r}"

        self.finish()

    def tick(self):
        if self.control and (self.control.stop or self.control.paused):
            return
        self.ticks += 1
        self.state.data.setdefault("stats", {})
        self.state.data["stats"]["ticks"] = int(self.state.data["stats"].get("ticks") or 0) + 1

        if not self.observe():
            return
        if not self._last_status.get("alive", True):
            self._wait_respawn()
            return
        if self.guard():
            self.state.save()
            return

        if self._stagnant >= 4:
            print(f"  🧱 连续 {self._stagnant} 轮没有进展（位置/背包/进度都没变）")
            self.traj.log("stagnation", ticks=self._stagnant)
            decision = self._forced_progress()
            self._stagnant = 0
        else:
            decision = self.decide()
            decision = self._avoid_blocked(decision)
            decision = self._avoid_repeat(decision)
        if self.control and (self.control.stop or self.control.paused):
            return
        signature = action_signature(
            decision.get("skill"), decision.get("args"), self.state)
        if self.loop_guard.hit(signature):
            print(f"  🔁 连续 {self.loop_guard.limit} 轮相同动作且状态没变，判定死循环，自动停止")
            self.traj.log("loop_stop", signature=signature)
            self.stop = True
            self.stop_reason = f"连续 {self.loop_guard.limit} 轮死循环"
            return
        self.execute(decision)
        self._update_stagnation()
        self.state.save()

    # ================================================================
    # 观察
    # ================================================================
    def observe(self):
        started = time.perf_counter()
        try:
            status = self.api.status() or {}
        except McApiError as e:
            self.status_stats.record_error()
            self._on_api_error(e)
            return False

        status_ms = (time.perf_counter() - started) * 1000
        self.status_stats.record(status_ms)
        net = self.state.data.setdefault("net", {})
        net["status_ms"] = round(status_ms, 1)
        net["status"] = self.status_stats.summary()
        self.traj.log("latency", target="status", ms=round(status_ms, 1))

        warn_ms = float(getattr(self.cfg, "NET_LATENCY_WARN_MS", 3000))
        if status_ms >= warn_ms and time.time() - self._last_net_warn > 60:
            self._last_net_warn = time.time()
            print(f"  🌐 状态接口延迟 {status_ms:.0f}ms（阈值 {warn_ms:.0f}ms），网络可能不稳")
            self.traj.log("net_slow", target="status", ms=round(status_ms, 1))

        self.state.update_status(status)
        self._last_status = status
        if status.get("ready") is False:
            self._log("wait", reason="ready=false（可能在大厅/切服）")
            time.sleep(3)
            return False

        if self.state.inventory_age() > float(self.cfg.INVENTORY_TTL_S):
            try:
                self.state.update_inventory(self.api.inventory())
            except McApiError:
                pass

        self._poll_events()
        self._maybe_refresh_advancements()
        return True

    def _poll_events(self):
        try:
            data = self.api.events(limit=30) or {}
        except McApiError:
            return
        for event in data.get("events") or []:
            kind = event.get("type")
            if kind == "death":
                self.state.note_death()
                self.state.add_event("死亡")
                self._log("event", type="death", cause=event.get("cause"))
            elif kind == "hurt":
                damage = event.get("damage") or 0
                attacker = event.get("likely_attacker") or {}
                if not isinstance(attacker, dict):
                    attacker = {}
                self._last_hurt_at = time.time()
                self._last_hurt_damage = float(damage or 0)
                self._last_hurt_by = attacker.get("name")
                self._last_hurt_is_player = (
                    attacker.get("type") == "player"
                    or (self._last_hurt_by or "").lower() == "player"
                )
                text = f"受伤 -{damage} {self._last_hurt_by or ''}"
                self.state.add_event(text.strip())
            elif kind == "chat":
                self.state.add_event(f"聊天：{event.get('text')}")
            elif kind in ("kicked", "disconnected"):
                self.state.add_event(f"掉线：{event.get('reason')}")
                self._on_api_error(McApiError("not_connected", "被踢/掉线"))
            elif kind == "death_loop":
                self.state.add_event("触发死亡循环保护")
                self._sleep(int(self.cfg.SLEEP_AFTER_DEATH_LOOP_S))

    def _maybe_refresh_advancements(self, force=False):
        now = time.time()
        if not force and now - self._last_adv < float(self.cfg.ADVANCEMENT_REFRESH_S):
            return []
        return self.refresh_advancements(force=force)

    def refresh_advancements(self, force=False):
        try:
            result = self.api.advancements(include_recipes=False) or {}
        except McApiError as e:
            if e.code not in ("not_ready", "busy", "wrong_server"):
                self.state.add_event(f"进度获取失败：{e.code}")
            return []
        if result.get("ready") is False and not result.get("advancements"):
            return []

        new_items = self.state.update_advancements(result, self.scorer)
        self._last_adv = time.time()
        for item in new_items:
            self.new_advancements.append(item)
            print(f"  🏆 完成进度：{item.get('title')}（+{item.get('score')} 分）")
            self.traj.log("advancement", id=item.get("id"), title=item.get("title"),
                          score=item.get("score"), frame=item.get("frame"))
        if new_items:
            print(f"     累计预计得分：{self.state.score_total()} 分")
            self.state.save()
        return new_items

    # ================================================================
    # 保命
    # ================================================================
    def _alive(self):
        try:
            status = self.api.status() or {}
        except McApiError:
            return True
        return bool(status.get("alive", True))

    def guard(self):
        """吃东西 / 逃跑 / 避让死亡循环。返回 True 表示这一圈不做别的事了。"""
        # 1) 死亡循环保护
        if len(self.state.recent_deaths(self.cfg.DEATH_WINDOW_S)) >= int(self.cfg.DEATH_LIMIT):
            print(f"  🛑 检测到连续死亡，主动休息 {self.cfg.SLEEP_AFTER_DEATH_LOOP_S} 秒，"
                  f"避免被服务器强制下线 5 分钟")
            self._log("guard", reason="death_loop", sleep=self.cfg.SLEEP_AFTER_DEATH_LOOP_S)
            self._sleep(int(self.cfg.SLEEP_AFTER_DEATH_LOOP_S))
            return True

        # 2) 刚被打：立刻反应（玩家/怪）；环境伤害（岩浆/摔落）没有 attacker 就不跑
        if self._last_hurt_at and time.time() - self._last_hurt_at < 3:
            if time.time() - self._last_flee_at > 3:
                attacker = self._last_hurt_by
                is_player = self._last_hurt_is_player
                if not attacker:
                    # 接口可能没给 likely_attacker（比如玩家打你），自己查一下附近
                    try:
                        found = self.api.get_entities(
                            radius=10, types=["player", "hostile"]) or {}
                        ents = found.get("entities") or []
                        if ents:
                            attacker = ents[0].get("name")
                            is_player = ents[0].get("type") == "player"
                    except McApiError:
                        pass
                if attacker:
                    print(f"  🛡 被 {attacker} 打了，先撤（{'玩家' if is_player else '怪物'}）")
                    self._last_flee_at = time.time()
                    self._run_safety("flee", {
                        "distance": int(self.cfg.FLEE_DISTANCE),
                        "include_players": True,
                    })
                    return True

        # 3) 饿了就吃
        food = self.state.data.get("food")
        if isinstance(food, (int, float)) and food < int(self.cfg.EAT_BELOW_FOOD):
            if self._has_food():
                self._run_safety("eat", {})
                return True

        # 3) 血太少 + 附近有怪 → 跑
        health = self.state.data.get("health")
        if isinstance(health, (int, float)) and health <= int(self.cfg.FLEE_BELOW_HEALTH):
            if self.state.data.get("hostile"):
                self._run_safety("flee", {"distance": int(self.cfg.FLEE_DISTANCE)})
                return True

        return False

    def _has_food(self):
        inv = self.state.inventory()
        return any(int(inv.get(name, 0)) > 0 for name in skills.FOODS)

    def _run_safety(self, skill, args):
        try:
            result = self._run_skill(skill, args)
            summary = result.get("summary", skill)
            print(f"  🛟 {summary}")
            self.state.add_recent(skill, True, summary)
            self.state.bump(skill)
            self.traj.log("safety", skill=skill, ok=True, summary=summary)
        except (skills.SkillError, McApiError) as e:
            message = getattr(e, "message", str(e))
            self.state.add_recent(skill, False, message)
            self.traj.log("safety", skill=skill, ok=False, error=message)

    def _run_skill(self, skill, args):
        from skills_registry import execute
        return execute(self.api, self.state, skill, args)

    # ================================================================
    # 决策
    # ================================================================
    def decide(self):
        if float(self.cfg.DECISION_MIN_INTERVAL) > 0:
            gap = time.time() - self._last_decision
            if gap < float(self.cfg.DECISION_MIN_INTERVAL):
                time.sleep(float(self.cfg.DECISION_MIN_INTERVAL) - gap)

        if self.decide_locally:
            decision = self.planner._fallback(self.state, reason="本地策略模式（不调用模型）")
            return self._log_decision(decision, local=True)

        time_left = (self.deadline - time.time()) / 60.0 if self.deadline else None
        elapsed = max(0.0, (time.time() - self._started_at) / 60.0)
        llm_started = time.perf_counter()
        llm_ms = None
        try:
            decision = self.planner.decide(
                self.state,
                time_left_min=time_left,
                tokens_left=self.llm.remaining_input(),
                elapsed_min=elapsed,
            )
            self._llm_errors = 0
            llm_ms = (time.perf_counter() - llm_started) * 1000
            net = self.state.data.setdefault("net", {})
            net["llm_ms"] = round(llm_ms, 1)
            net["llm"] = self.llm_stats.summary()
            warn_ms = float(getattr(self.cfg, "NET_LATENCY_WARN_MS", 3000))
            if llm_ms >= warn_ms and time.time() - self._last_net_warn > 60:
                self._last_net_warn = time.time()
                print(f"  🌐 大模型响应 {llm_ms:.0f}ms（阈值 {warn_ms:.0f}ms），注意节奏")
                self.traj.log("net_slow", target="llm", ms=round(llm_ms, 1))
        except BudgetExceeded:
            raise
        except LLMFatalError as e:
            self.stop = True
            self.stop_reason = f"大模型不可用：{e}"
            print(f"  🛑 {self.stop_reason}")
            print("     修好 key/余额后重跑；也可以先用 `python main.py run --no-llm` 测局域网。")
            self.traj.log("fatal", error=str(e))
            return {"skill": "__finish__", "args": {}, "why": "模型不可用"}
        except (LLMError, ValueError) as e:
            self._llm_errors += 1
            print(f"  ⚠️ 决策失败（第 {self._llm_errors} 次）：{e}")
            self.traj.log("llm_error", error=str(e), count=self._llm_errors)
            if self._llm_errors >= 3:
                self.stop = True
                self.stop_reason = f"连续 {self._llm_errors} 次模型调用失败：{e}"
                print(f"  🛑 {self.stop_reason}")
                return {"skill": "__finish__", "args": {}, "why": "模型连续失败"}
            self._sleep(min(10, 2 * self._llm_errors))
            decision = self.planner._fallback(self.state, reason="模型出错，走兜底")

        return self._log_decision(decision, llm_ms=llm_ms)

    def _log_decision(self, decision, local=False, llm_ms=None):
        self._last_decision = time.time()
        if local:
            suffix = "（本地策略）"
        elif llm_ms is not None:
            suffix = f"（LLM {llm_ms:.0f}ms）"
        else:
            suffix = "（LLM 失败，兜底）"
        print(f"  🧭 决策{suffix}：{decision['skill']}("
              f"{json.dumps(decision.get('args') or {}, ensure_ascii=False)})"
              f" —— {decision.get('why', '')}")
        self.traj.log("decision", skill=decision["skill"], args=decision.get("args"),
                      target=decision.get("target"), why=decision.get("why"),
                      local=local, llm_ms=round(llm_ms, 1) if llm_ms else None,
                      tokens=self.state.data.get("tokens"))
        return decision

    # ================================================================
    # 每次交互的延迟
    # ================================================================
    def _on_api_request(self, method, path, ms, ok):
        if ok:
            self.api_call_stats.record(ms)
        else:
            self.api_call_stats.record_error()
        if self._skill_active:
            self._skill_api_calls += 1
            self._skill_api_ms += ms
        net = self.state.data.setdefault("net", {})
        net["api_ms"] = round(ms, 1)
        net["api"] = self.api_call_stats.summary()
        self.traj.log("latency", target="api", method=method, path=path,
                      ms=round(ms, 1), ok=ok)
        if getattr(self.cfg, "PRINT_LATENCY", True):
            mark = "" if ok else "（失败）"
            print(f"      ↔ {method} /{str(path).lstrip('/')} {ms:.0f}ms{mark}")

    def _on_llm_call(self, ms, ok):
        if ok:
            self.llm_stats.record(ms)
        else:
            self.llm_stats.record_error()
        self.traj.log("latency", target="llm", ms=round(ms, 1), ok=ok)
        if getattr(self.cfg, "PRINT_LATENCY", True):
            print(f"      🤖 LLM {ms:.0f}ms{'' if ok else '（失败）'}")

    def _skill_latency_text(self):
        if not self._skill_api_calls:
            return ""
        return f"，API {self._skill_api_calls} 次 {self._skill_api_ms / 1000:.1f}s"

    # ================================================================
    # 执行
    # ================================================================
    def execute(self, decision):
        skill = decision.get("skill")
        if skill in ("__finish__", "stop", "done"):
            self.stop = True
            self.stop_reason = "模型认为可以收工"
            print("  🏁 模型选择结束")
            return

        args = decision.get("args") or {}
        key = self._fail_key(skill, args)
        if self.state.is_blacklisted(key):
            print(f"  ⏭️ {skill} 还在拉黑期，跳过")
            self.state.add_recent(skill, False, "黑名单，跳过")
            self.traj.log("skip_blocked", skill=skill, args=args)
            return

        started = time.time()
        self._skill_active = True
        self._skill_api_calls = 0
        self._skill_api_ms = 0.0
        try:
            result = self._run_skill(skill, args)
            elapsed = time.time() - started
            summary = result.get("summary", skill)
            print(f"  ✅ {summary}（{elapsed:.0f}s{self._skill_latency_text()}）")
            self.state.bump(skill)
            self.state.add_recent(skill, True, summary)
            self.state.clear_failure(key)
            self.state.clear_failures_for(skill)
            self.state.data["inventory_at"] = 0.0     # 动作后背包一定变了，下一圈重新拉
            self.traj.log("skill", skill=skill, args=args, ok=True,
                          ms=int(elapsed * 1000), summary=summary)
            self._log("result", skill=skill, ok=True, summary=summary)
            # 这几类动作很可能刚完成一个 advancement：顺手刷一下（限频，别太贵）
            if skill in ("craft", "smelt", "place", "collect", "chop_tree") and \
                    time.time() - self._last_adv > 15:
                self._maybe_refresh_advancements(force=True)
        except (skills.SkillError, McApiError) as e:
            elapsed = time.time() - started
            code = getattr(e, "code", "error")
            message = getattr(e, "message", str(e))

            if code in _TRANSIENT_CODES or skill in _OBSERVE_SKILLS:
                print(f"  ⚠️ {skill} 未成功（{code}，不拉黑）：{message}{self._skill_latency_text()}")
                self.state.add_recent(skill, False, f"{code}: {message}")
                self.traj.log("skill", skill=skill, args=args, ok=False,
                              ms=int(elapsed * 1000), error=code, message=message,
                              blacklisted=False)
                self._sleep(1.0)
            else:
                count = self.state.record_failure(
                    key,
                    blacklist_after=int(self.cfg.FAIL_BLACKLIST_COUNT),
                    blacklist_seconds=int(self.cfg.FAIL_BLACKLIST_SECONDS),
                )
                print(f"  ❌ {skill} 失败：{code} {message}（第 {count} 次{self._skill_latency_text()}）")
                self.state.add_recent(skill, False, f"{code}: {message}")
                self.traj.log("skill", skill=skill, args=args, ok=False,
                              ms=int(elapsed * 1000), error=code, message=message,
                              blacklisted=count >= int(self.cfg.FAIL_BLACKLIST_COUNT))
                self._log("result", skill=skill, ok=False, error=code, message=message)
        except Exception as e:  # 技能里的意外 bug 不能打断整场比赛，也不拉黑
            message = f"{type(e).__name__}: {e}"
            print(f"  ❌ {skill} 意外错误（不拉黑）：{message}")
            self.state.add_recent(skill, False, message)
            self.traj.log("skill", skill=skill, args=args, ok=False, error="unexpected",
                          message=message, blacklisted=False)
            self._sleep(1.0)
        finally:
            self._skill_active = False
            self.state.save()

    # ================================================================
    # 防死循环 / 防空转
    # ================================================================
    def _avoid_blocked(self, decision):
        """发现动作被拉黑，就换一个能推进的，而不是傻等下一轮再选同一个。"""
        skill = decision.get("skill")
        args = decision.get("args") or {}
        key = self._fail_key(skill, args)
        if not self.state.is_blacklisted(key):
            return decision
        print(f"  ⏭️ 跳过 {skill}（最近失败太多次，改做能推进的）")
        self.state.add_recent(skill, False, "黑名单，跳过")
        self.traj.log("skip_blocked", skill=skill, args=args)
        return self._forced_progress()

    def _avoid_repeat(self, decision):
        """同一个动作连续做 3 次（哪怕成功）也强制换一个，避免原地空转。"""
        key = self._fail_key(decision.get("skill"), decision.get("args"))
        if key == self._last_decision_key:
            self._same_decision += 1
        else:
            self._same_decision = 1
            self._last_decision_key = key
        if self._same_decision < 3:
            return decision
        print("  🔂 同一个动作连续 3 次，强制换一个")
        self.traj.log("anti_repeat", skill=decision.get("skill"))
        alt = self._forced_progress()
        self._same_decision = 0
        self._last_decision_key = self._fail_key(alt.get("skill"), alt.get("args"))
        return alt

    def _forced_progress(self):
        """挑一个「能改变状态」的动作，避开拉黑；全被拉黑就清掉再试。"""
        inv = self.state.inventory()
        logs = sum(int(v) for k, v in inv.items()
                   if k.endswith("_log") or k.endswith("_wood"))
        candidates = []
        if not self.state.data.get("island"):
            candidates.append(("survey", {}))
        if logs < 6:
            candidates.append(("chop_tree", {}))
        candidates += [
            ("collect", {"group": "stone", "count": 4}),
            ("explore", {"distance": 12}),
            ("scan", {"radius": 12}),
        ]
        for skill, args in candidates:
            key = self._fail_key(skill, args)
            if self.state.is_blacklisted(key) or key == self._last_decision_key:
                continue
            print(f"  🛟 强制推进：{skill}")
            self.traj.log("anti_stagnation", skill=skill, args=args)
            return {"skill": skill, "args": args, "why": "无进展，强制推进"}

        # 所有候选都被拉黑：清掉砍树/勘察的拉黑，强制做一次
        self.state.clear_failures_for("chop_tree")
        self.state.clear_failures_for("survey")
        skill = "chop_tree" if logs < 6 else "survey"
        print(f"  🧹 所有候选都被拉黑，清空后强制做：{skill}")
        self.traj.log("anti_stagnation", skill=skill, cleared=True)
        return {"skill": skill, "args": {}, "why": "清空拉黑后强制推进"}

    def _update_stagnation(self):
        pos = self.state.data.get("pos") or [0, 0, 0]
        sig = (
            tuple(round(float(v), 0) for v in pos),
            tuple(sorted(self.state.inventory().items())),
            len(self.state.data.get("advancements") or {}),
            (self.state.data.get("target") or {}).get("id"),
        )
        if sig == self._progress_sig:
            self._stagnant += 1
            if self._stagnant in (4, 8, 16):
                self.traj.log("stagnation", ticks=self._stagnant)
        else:
            self._stagnant = 0
            self._progress_sig = sig

    # ================================================================
    # 收尾
    # ================================================================
    def finish(self):
        print("\n" + "=" * 58)
        print("正在收尾：保存状态 + 拉取最终进度……")
        try:
            self.refresh_advancements(force=True)
        except Exception:
            pass
        self.state.save()

        ended = time.time()
        usage = dict(self.state.data.get("tokens") or self.llm.usage)
        deaths = len((self.state.data.get("stats") or {}).get("deaths") or [])

        log_path = os.path.join(_HERE, self.cfg.TASK_LOG_FILE)
        if getattr(self.cfg, "TASK_LOG_AT_ROOT", True):
            log_path = os.path.join(os.path.dirname(_HERE), self.cfg.TASK_LOG_FILE)

        write_task_log(
            log_path,
            started_at=self._started_at,
            ended_at=ended,
            ticks=self.ticks,
            actions=int((self.state.data.get("stats") or {}).get("actions") or 0),
            new_advancements=self.new_advancements,
            total_score=self.state.score_total(),
            usage=usage,
            deaths=deaths,
            trajectory_path=self.traj.path,
            stop_reason=self.stop_reason or "达到运行条件",
            input_budget=getattr(self.cfg, "INPUT_TOKEN_BUDGET", None),
            output_budget=getattr(self.cfg, "OUTPUT_TOKEN_BUDGET", None),
            net_stats={"status": self.status_stats.summary(),
                       "llm": self.llm_stats.summary(),
                       "api": self.api_call_stats.summary()},
        )
        self.traj.log("finish", reason=self.stop_reason, score=self.state.score_total(),
                      advancements=len(self.new_advancements), usage=usage,
                      net={"status": self.status_stats.summary(),
                           "llm": self.llm_stats.summary(),
                           "api": self.api_call_stats.summary()})
        self.traj.close()

        print(f"  📄 任务记录已写入：{log_path}")
        print(f"  🧾 轨迹文件：{self.traj.path}")
        print(f"  🏆 本次新完成进度 {len(self.new_advancements)} 项，累计预计 {self.state.score_total()} 分")
        print(f"  🌐 网络延迟：状态接口 {self.status_stats.text()}；"
              f"LLM {self.llm_stats.text()}；API 请求 {self.api_call_stats.text()}")
        print(f"  🪙 {self.llm.usage_text()}")
        print(f"  结束原因：{self.stop_reason}")
        print("=" * 58)

    # ================================================================
    # 小工具
    # ================================================================
    def _should_stop(self, max_ticks):
        if self.stop:
            return True
        if self._stop_at and time.time() >= self._stop_at:
            self.stop_reason = "到达结束时间（留出收尾时间）"
            return True
        if max_ticks and self.ticks >= int(max_ticks):
            self.stop_reason = f"达到最大轮数 {max_ticks}"
            return True
        return False

    def _on_api_error(self, error):
        code = getattr(error, "code", "error")
        message = getattr(error, "message", str(error))
        print(f"  ⚠️ 接口错误：{code} {message}")
        self.traj.log("api_error", code=code, message=message)

        if code in ("invalid_token", "unauthorized"):
            try:
                self.api.login()
                print("  ✅ 已重新登录")
            except Exception as e:
                print("  ❌ 重新登录失败：", e)
                self._sleep(15)
            return
        if code in ("not_connected", "disconnected", "kicked"):
            try:
                self.api.connect()
                print("  ✅ 已重新连接")
            except Exception:
                pass
            self._sleep(8)
            return
        if code == "death_loop_cooldown":
            self._sleep(int(self.cfg.SLEEP_AFTER_DEATH_LOOP_S))
            return
        if code == "dead":
            self._wait_respawn()
            return
        self._sleep(3)

    def _wait_respawn(self, timeout=90):
        print("  ☠️ 已死亡，等待重生……")
        self.state.note_death()
        end = time.time() + timeout
        while time.time() < end and not self.stop:
            time.sleep(3)
            try:
                status = self.api.status() or {}
            except McApiError as e:
                if getattr(e, "code", "") == "dead":
                    continue
                self._on_api_error(e)
                continue
            if status.get("alive", True) and status.get("ready", True):
                self.state.update_status(status)
                print("  ✅ 已重生")
                return True
        return False

    def _sleep(self, seconds):
        end = time.time() + float(seconds)
        while time.time() < end and not self.stop:
            time.sleep(min(2.0, max(0.1, end - time.time())))

    @staticmethod
    def _fail_key(skill, args):
        try:
            payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = str(args)
        return f"{skill}:{payload}"[:140]

    def _log(self, kind, **fields):
        if self.verbose:
            self.traj.log(kind, **fields)

    def _banner(self, deadline):
        remaining = ""
        if deadline:
            left = max(0, int(deadline - time.time()))
            remaining = f"（还剩 {left // 3600} 小时 {left % 3600 // 60} 分）"
        print("=" * 58)
        print(" 自主运行模式：advancement 驱动，无人值守")
        print(f" 结束时间：{time.strftime('%H:%M', time.localtime(deadline)) if deadline else '不设限制'}{remaining}")
        print(f" token 预算：输入 {self.cfg.INPUT_TOKEN_BUDGET:,} / 输出 {self.cfg.OUTPUT_TOKEN_BUDGET:,}")
        print(f" 状态文件：{self.state.path}")
        print(f" 轨迹文件：{self.traj.path}")
        print(f" 已完成进度：{len(self._started_ids)} 项（预计 {self.state.score_total()} 分）")
        print("=" * 58)

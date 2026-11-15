# -*- coding: utf-8 -*-
"""离线单元测试：不联网、不花 token。

跑法（在 Codes 目录）：
    python -m unittest discover -s tests -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import advancements
import chat as chat_mod
import config
import hotkeys as hotkeys_mod
import knowledge as knowledge_mod
import learn as learn_mod
import manual as manual_mod
import skills
import strategy as strategy_mod
from agent import Agent
from llm import BudgetExceeded, LLMClient, LLMError, LLMFatalError, extract_json
from mc_api import McApi, McApiError
from mock_api import MockApi, ScriptedLLM
from my_server.adapter import MyServerApi
from netcheck import LatencyStats
from planner import Planner
from report import format_report, summarize
from world_state import WorldState


# =====================================================================
# 假 HTTP
# =====================================================================
class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {}, ensure_ascii=False)
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _take(self):
        if not self.responses:
            raise AssertionError("FakeSession 没有更多响应了")
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._take()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._take()


# =====================================================================
# 1. JSON 解析
# =====================================================================
class TestJsonParsing(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(extract_json('{"skill":"scan"}'), {"skill": "scan"})

    def test_code_fence(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prose_wrapped(self):
        self.assertEqual(extract_json('我觉得应该这样：{"skill":"scan"} 就这样'), {"skill": "scan"})

    def test_truncated_returns_none(self):
        self.assertIsNone(extract_json('{"a": [1, 2'))

    def test_braces_inside_string(self):
        self.assertEqual(extract_json('{"msg":"用 {花括号} 测试"}'), {"msg": "用 {花括号} 测试"})

    def test_two_objects_takes_first(self):
        self.assertEqual(extract_json('{"a":1}{"b":2}'), {"a": 1})


# =====================================================================
# 2. advancement 计分
# =====================================================================
class TestAdvancements(unittest.TestCase):
    def test_root_is_zero(self):
        self.assertEqual(advancements.score("minecraft:story/root"), 0)

    def test_known_entry(self):
        # 规则 v2：分值只由难度决定，难度 1 → 5 分
        self.assertEqual(advancements.score("minecraft:story/mine_stone"), 5)

    def test_high_value_entry(self):
        # 内置近似表：难度 5 → 100 分
        self.assertEqual(advancements.score("minecraft:end/kill_dragon"), 100)

    def test_unknown_falls_back_to_frame(self):
        # goal → 难度 3 → 30 分
        self.assertEqual(advancements.score("minecraft:foo/bar/baz", frame="goal"), 30)

    def test_table_override(self):
        table = {"minecraft:story/mine_stone": {"difficulty": 5, "score": 100}}
        self.assertEqual(advancements.score("minecraft:story/mine_stone", table=table), 100)

    def test_unscored_table_entry_is_zero(self):
        table = {"minecraft:end/kill_dragon": {"difficulty": 4, "score": 0, "scored": False}}
        self.assertEqual(advancements.score("minecraft:end/kill_dragon", table=table), 0)

    def test_official_table_file(self):
        path = os.path.join(HERE, "data", "advancement_scores.json")
        table = advancements.load_table(path)
        self.assertGreater(len(table), 90)
        self.assertEqual(advancements.score("minecraft:story/mine_stone", table=table), 5)
        self.assertEqual(advancements.score("minecraft:end/kill_dragon", table=table), 0)
        self.assertEqual(advancements.score("minecraft:adventure/arbalistic", table=table), 100)

    def test_load_table_dict_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"minecraft:a/b": {"difficulty": 2, "depth": 1}}, f)
            self.assertEqual(advancements.load_table(path)["minecraft:a/b"]["difficulty"], 2)

            with open(path, "w", encoding="utf-8") as f:
                json.dump([{"id": "minecraft:c/d", "difficulty": 4, "depth": 2}], f)
            table = advancements.load_table(path)
            self.assertEqual(table["minecraft:c/d"]["depth"], 2)

    def test_broken_table_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{这不是 json")
            self.assertEqual(advancements.load_table(path), {})


# =====================================================================
# 3. 世界状态
# =====================================================================
class TestWorldState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")
        self.state = WorldState(self.path).load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_and_inventory(self):
        self.state.update_status({
            "pos": [1.25, 64.0, -3.5], "health": 15, "food": 9, "dimension": "overworld",
            "held": {"name": "stone_pickaxe", "count": 1},
            "nearest_hostile": {"name": "zombie", "dist": 6.0},
        })
        self.state.update_inventory({"items": [{"name": "oak_log", "count": 3}], "held": None})
        self.assertEqual(self.state.data["pos"], [1.25, 64.0, -3.5])
        self.assertEqual(self.state.inventory(), {"oak_log": 3})
        self.assertTrue(self.state.has("oak_log", 2))
        self.assertEqual(self.state.data["hostile"]["name"], "zombie")

    def test_advancements_merge(self):
        result = {"advancements": [
            {"id": "minecraft:story/mine_stone", "title": "Stone Age", "frame": "task",
             "status": "completed"},
            {"id": "minecraft:story/mine_diamond", "title": "Diamonds!", "frame": "goal",
             "status": "in_progress"},
        ]}
        new = self.state.update_advancements(result, lambda adv_id, frame: advancements.score(adv_id, frame))
        self.assertEqual(len(new), 1)
        self.assertEqual(self.state.score_total(), 5)
        self.assertEqual(self.state.data["pending"][0]["id"], "minecraft:story/mine_diamond")
        # 再合并一次不会重复计分
        self.state.update_advancements(result, lambda adv_id, frame: advancements.score(adv_id, frame))
        self.assertEqual(self.state.score_total(), 5)

    def test_pending_excludes_unscored(self):
        result = {"advancements": [
            {"id": "minecraft:end/kill_dragon", "title": "Free the End", "frame": "goal",
             "status": "in_progress"},
        ]}
        table = {"minecraft:end/kill_dragon": {"difficulty": 4, "score": 0, "scored": False}}
        self.state.update_advancements(result, lambda adv_id, frame: advancements.score(
            adv_id, frame, table))
        self.assertEqual(self.state.data["pending"], [])

    def test_clear_failures_for_skill(self):
        self.state.record_failure("craft:{\"item\":\"x\"}", blacklist_after=1, blacklist_seconds=60)
        self.state.record_failure("craft:{\"item\":\"y\"}", blacklist_after=1, blacklist_seconds=60)
        self.assertTrue(self.state.blocked_lines())
        self.state.clear_failures_for("craft")
        self.assertEqual(self.state.data["failures"], {})
        self.assertEqual(self.state.data["blacklist"], {})
        self.assertEqual(self.state.blocked_lines(), [])

    def test_failure_blacklist(self):
        key = "dig@1,2,3"
        self.state.record_failure(key, blacklist_after=2, blacklist_seconds=60)
        self.assertFalse(self.state.is_blacklisted(key))
        self.state.record_failure(key, blacklist_after=2, blacklist_seconds=60)
        self.assertTrue(self.state.is_blacklisted(key))

    def test_save_and_reload(self):
        self.state.update_inventory({"items": [{"name": "bread", "count": 2}]})
        self.state.save()
        again = WorldState(self.path).load()
        self.assertEqual(again.inventory(), {"bread": 2})

    def test_corrupted_file_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{坏了")
        state = WorldState(self.path).load()
        self.assertEqual(state.inventory(), {})


# =====================================================================
# 4. 大模型客户端（计量 / 预算 / 重试）
# =====================================================================
class TestLLMClient(unittest.TestCase):
    def _client(self, responses, **kwargs):
        session = FakeSession(responses)
        client = LLMClient("https://example.com/v1", "test-model", "sk-test",
                           session=session, **kwargs)
        return client, session

    def test_success_records_usage(self):
        client, _ = self._client([FakeResponse(payload={
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })])
        self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "hello")
        self.assertEqual(client.usage, {"input": 10, "output": 5, "calls": 1})
        self.assertEqual(client.latency_stats()["n"], 1)

    def test_call_callback(self):
        session = FakeSession([FakeResponse(payload={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })])
        calls = []
        client = LLMClient("https://example.com/v1", "m", "k", session=session,
                           on_call=lambda ms, ok: calls.append((ms, ok)))
        client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1])
        self.assertGreaterEqual(calls[0][0], 0)

    def test_budget_exceeded(self):
        client, _ = self._client([FakeResponse(payload={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        })], input_budget=50, output_budget=1000, stop_ratio=0.5)
        with self.assertRaises(BudgetExceeded):
            client.chat([{"role": "user", "content": "hi"}])

    def test_auth_error_is_fatal(self):
        client, _ = self._client([FakeResponse(status=401, payload={"error": "bad key"})])
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("API Key", str(ctx.exception))

    def test_retry_on_429(self):
        client, session = self._client([
            FakeResponse(status=429, payload={}, text="too many", headers={}),
            FakeResponse(payload={"choices": [{"message": {"content": "ok"}}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        ], retries=2)
        with mock.patch("llm.time.sleep", return_value=None):
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "ok")
        self.assertEqual(len(session.calls), 2)

    def test_json_mode_fallback_on_400(self):
        client, session = self._client([
            FakeResponse(status=400, payload={}, text="response_format not supported"),
            FakeResponse(payload={"choices": [{"message": {"content": '{"a":1}'}}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        ])
        self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), '{"a":1}')
        self.assertFalse(client.json_mode)
        self.assertEqual(len(session.calls), 2)

    def test_chat_json_retries_once(self):
        client, _ = self._client([
            FakeResponse(payload={"choices": [{"message": {"content": "不是 JSON"}}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
            FakeResponse(payload={"choices": [{"message": {"content": '{"skill":"scan"}'}}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        ])
        self.assertEqual(client.chat_json([{"role": "user", "content": "hi"}]), {"skill": "scan"})


# =====================================================================
# 5. 比赛服客户端（错误映射）
# =====================================================================
class TestMcApi(unittest.TestCase):
    def test_result_unwrapped(self):
        api = McApi("https://example.com/api/v1", "u", "p")
        api._session = FakeSession([FakeResponse(payload={"ok": True, "result": {"pos": [1, 2, 3]}})])
        self.assertEqual(api.status(), {"pos": [1, 2, 3]})
        self.assertEqual(api.latency_stats()["n"], 1)

    def test_request_callback(self):
        events = []
        api = McApi("https://example.com/api/v1", "u", "p",
                    on_request=lambda method, path, ms, ok: events.append((method, path, ok)))
        api._session = FakeSession([FakeResponse(payload={"ok": True, "result": {}})])
        api.status()
        self.assertEqual(events[0][0], "GET")
        self.assertIn("status", events[0][1])
        self.assertTrue(events[0][2])

    def test_stop_on_passed(self):
        api = McApi("https://example.com/api/v1", "u", "p")
        captured = []
        api._request = lambda method, path, body=None: captured.append((path, body)) or {}
        api.move_to(x=1, z=2, stop_on=["hurt"])
        self.assertEqual(captured[-1][0], "move_to")
        self.assertEqual(captured[-1][1]["stop_on"], ["hurt"])
        api.attack(entity_id=7, stop_on=["death"])
        self.assertEqual(captured[-1][1]["stop_on"], ["death"])

    def test_all_spec_endpoints_present(self):
        expected = {
            "login", "logout", "connect", "disconnect", "status", "inventory",
            "find_blocks", "get_block", "get_entities", "events", "advancements",
            "move_to", "dig", "place", "interact", "use_item", "attack", "shoot",
            "craft", "window_view", "window_act", "equip", "posture", "dismount",
            "drop", "chat", "stop",
        }
        api = McApi("https://example.com/api/v1", "u", "p")
        self.assertFalse(expected - set(dir(api)))

    def test_business_error_raises(self):
        api = McApi("https://example.com/api/v1", "u", "p")
        api._session = FakeSession([FakeResponse(payload={
            "ok": False, "error": {"code": "path_not_found", "message": "No path"}})])
        with self.assertRaises(McApiError) as ctx:
            api.dig(pos=[0, 0, 0])
        self.assertEqual(ctx.exception.code, "path_not_found")

    def test_http_error_maps_code(self):
        api = McApi("https://example.com/api/v1", "u", "p")
        api._session = FakeSession([FakeResponse(status=401, payload={
            "error": {"code": "invalid_credentials", "message": "bad"}})])
        with self.assertRaises(McApiError) as ctx:
            api.login()
        self.assertEqual(ctx.exception.code, "invalid_credentials")


# =====================================================================
# 6. 技能（用假服务器）
# =====================================================================
class TestSkills(unittest.TestCase):
    def setUp(self):
        self.api = MockApi()
        self.tmp = tempfile.TemporaryDirectory()
        self.state = WorldState(os.path.join(self.tmp.name, "s.json")).load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_survey_island(self):
        result = skills.run_skill(self.api, self.state, "survey", {"radius": 16})
        self.assertIn("island", self.state.data)
        self.assertTrue(self.state.data["island"]["top"])
        self.assertIn("勘察", result["summary"])

    def test_chop_tree(self):
        result = skills.run_skill(self.api, self.state, "chop_tree", {})
        self.assertIn("summary", result)
        self.assertTrue(result.get("gained"))

    def test_craft_pickaxe_with_table(self):
        self.api.items["oak_log"] = 3
        result = skills.run_skill(self.api, self.state, "craft",
                                  {"item": "wooden_pickaxe", "count": 1})
        self.assertIn("wooden_pickaxe", self.api.items)
        self.assertIn("合成", result["summary"])

    def test_collect_stone_needs_pickaxe(self):
        # 没镐子：石头挖了也不掉（模拟原版），技能应报「什么都没挖到」
        with self.assertRaises(skills.SkillError):
            skills.run_skill(self.api, self.state, "collect",
                             {"group": "stone", "count": 4, "limit": 3})

    def test_missing_total_preferred(self):
        needs = skills._needs_from_extra({
            "example_recipe_needs": {"oak_log": 1},
            "missing_total": {"birch_log": 2},
        })
        self.assertEqual(needs, {"birch_log": 2})

    def test_collect_stone_with_pickaxe(self):
        self.api.items["wooden_pickaxe"] = 1
        result = skills.run_skill(self.api, self.state, "collect",
                                  {"group": "stone", "count": 3, "limit": 6})
        self.assertGreaterEqual(result["gained"].get("cobblestone", 0), 1)

    def test_eat(self):
        self.api.items["bread"] = 1
        self.api.food = 10.0
        result = skills.run_skill(self.api, self.state, "eat", {})
        self.assertGreater(self.api.food, 10.0)
        self.assertIn("bread", result["summary"])

    def test_unknown_skill(self):
        with self.assertRaises(skills.SkillError) as ctx:
            skills.run_skill(self.api, self.state, "fly_to_moon", {})
        self.assertEqual(ctx.exception.code, "unknown_skill")

    def test_smelt_iron(self):
        self.api.items.update({"wooden_pickaxe": 1, "coal": 2, "raw_iron": 1,
                               "cobblestone": 8, "furnace": 1})
        self.api.table_placed = True
        skills.run_skill(self.api, self.state, "place", {"item": "furnace"})
        with mock.patch("skills.time.sleep", return_value=None):
            result = skills.run_skill(self.api, self.state, "smelt",
                                      {"item": "raw_iron", "count": 1, "fuel": "coal"})
        self.assertIn("熔炼", result["summary"])
        self.assertIn("iron_ingot", self.api.items)


# =====================================================================
# 7. 规划层（提示词要短、坏输出要兜底）
# =====================================================================
class _CapturingLLM:
    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def chat_json(self, messages, **kwargs):
        self.messages = messages
        return self.reply


class TestPlanner(unittest.TestCase):
    def _state(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = WorldState(os.path.join(tmp.name, "s.json")).load()
        state.update_status({"pos": [0, 64, 0], "health": 20, "food": 20})
        state.update_inventory({"items": [{"name": "oak_log", "count": 3}]})
        return state

    def test_prompt_is_compact(self):
        llm = _CapturingLLM({"skill": "scan", "args": {}, "why": "看看"})
        planner = Planner(llm)
        planner.decide(self._state())
        total = sum(len(m["content"]) for m in llm.messages)
        self.assertLess(total, 6000, f"决策提示词太长：{total} 字符")

    def test_valid_decision(self):
        llm = _CapturingLLM({"skill": "chop_tree", "args": {}, "target": "", "why": "木头"})
        decision = Planner(llm).decide(self._state())
        self.assertEqual(decision["skill"], "chop_tree")

    def test_invalid_skill_falls_back(self):
        llm = _CapturingLLM({"skill": "launch_rocket", "args": {}})
        decision = Planner(llm).decide(self._state())
        self.assertIn(decision["skill"], skills.SKILLS)

    def test_fallback_surveys_then_chops(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty_state = WorldState(os.path.join(tmp.name, "s.json")).load()
        empty_state.update_status({"pos": [0, 64, 0], "health": 20, "food": 20})
        empty_state.update_inventory({"items": []})

        # 还不知道岛上有什么 → 先勘察
        llm = _CapturingLLM({"skill": "not_a_skill"})
        self.assertEqual(Planner(llm).decide(empty_state)["skill"], "survey")

        # 勘察过之后 → 先弄木头
        empty_state.data["island"] = {"features": {}, "top": {}}
        self.assertEqual(Planner(llm).decide(empty_state)["skill"], "chop_tree")


# =====================================================================
# 8. 主循环端到端（假服务器 + 假大脑）
# =====================================================================
def _cfg(tmp):
    cfg = SimpleNamespace()
    for name in dir(config):
        if name.isupper():
            setattr(cfg, name, getattr(config, name))
    cfg.LOG_DIR = tmp
    cfg.STATE_DIR = tmp
    cfg.TASK_LOG_FILE = os.path.join(tmp, "task.md")
    cfg.TASK_LOG_AT_ROOT = False
    cfg.ADVANCEMENT_REFRESH_S = 0
    cfg.INVENTORY_TTL_S = 0
    cfg.DECISION_MIN_INTERVAL = 0
    cfg.FAIL_BLACKLIST_COUNT = 3
    cfg.FAIL_BLACKLIST_SECONDS = 60
    cfg.SLEEP_AFTER_DEATH_LOOP_S = 1
    cfg.INPUT_TOKEN_BUDGET = 8_000_000
    cfg.OUTPUT_TOKEN_BUDGET = 1_500_000
    cfg.BUDGET_STOP_RATIO = 0.98
    return cfg


class TestAgentEndToEnd(unittest.TestCase):
    def test_runs_offline_and_records_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = MockApi()
            state = WorldState(os.path.join(tmp, "world_state.json")).load()
            agent = Agent(api, ScriptedLLM(), state, cfg=_cfg(tmp), verbose=False)
            with redirect_stdout(io.StringIO()):
                agent.run(deadline=time.time() + 40, max_ticks=10)

            self.assertGreaterEqual(agent.ticks, 5)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "task.md")))
            self.assertTrue(any(name.startswith("run-") for name in os.listdir(tmp)))
            self.assertGreater(len(state.data["advancements"]), 0)
            self.assertTrue(os.path.isfile(state.path))


# =====================================================================
# 9. 自定义服务器适配器
# =====================================================================
class TestMyServerAdapter(unittest.TestCase):
    def test_unwraps_ok_result(self):
        api = MyServerApi("https://my.example.com", "tok")
        api._session = FakeSession([FakeResponse(payload={
            "ok": True, "result": {"pos": [0, 64, 0], "health": 20}
        })])
        self.assertEqual(api.status()["health"], 20)

    def test_error_code(self):
        api = MyServerApi("https://my.example.com", "tok")
        api._session = FakeSession([FakeResponse(payload={
            "ok": False, "error": {"code": "busy", "message": "正在执行"}
        })])
        with self.assertRaises(McApiError) as ctx:
            api.dig(pos=[0, 0, 0])
        self.assertEqual(ctx.exception.code, "busy")

    def test_bearer_header(self):
        api = MyServerApi("https://my.example.com", "tok")
        session = FakeSession([FakeResponse(payload={"ok": True, "result": {}})])
        api._session = session
        api.status()
        headers = session.calls[0][2]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_empty_base_url_rejected(self):
        with self.assertRaises(ValueError):
            MyServerApi("", "tok")

    def test_stop_on_passed(self):
        api = MyServerApi("https://my.example.com", "tok")
        captured = []
        api._call = lambda method, path, body=None: captured.append((path, body)) or {}
        api.craft(item="oak_planks", count=4, stop_on=["hurt"])
        self.assertTrue(captured[-1][0].endswith("/craft"))
        self.assertEqual(captured[-1][1]["stop_on"], ["hurt"])


class TestAntiLoop(unittest.TestCase):
    def _agent(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        state = WorldState(os.path.join(tmp, "s.json")).load()
        state.data["island"] = {"features": {}}
        agent = Agent(MockApi(), ScriptedLLM(), state, cfg=_cfg(tmp), verbose=False)
        self.addCleanup(agent.traj.close)      # 先关文件，再删目录
        return state, agent

    def test_avoid_blocked_picks_progress_action(self):
        state, agent = self._agent()
        decision = {"skill": "craft", "args": {"item": "crafting_table", "count": 1}}
        state.data["blacklist"][agent._fail_key("craft", decision["args"])] = time.time() + 60

        alt = agent._avoid_blocked(decision)

        self.assertNotEqual(alt["skill"], "craft")

    def test_avoid_blocked_clears_when_everything_blocked(self):
        state, agent = self._agent()
        decision = {"skill": "craft", "args": {"item": "crafting_table"}}
        for skill, args in (
            ("craft", decision["args"]), ("chop_tree", {}), ("survey", {}),
            ("collect", {"group": "stone", "count": 4}),
            ("explore", {"distance": 12}), ("scan", {"radius": 12}),
        ):
            state.data["blacklist"][agent._fail_key(skill, args)] = time.time() + 60

        alt = agent._avoid_blocked(decision)

        self.assertIn(alt["skill"], ("chop_tree", "survey"))
        self.assertTrue(agent.state.data["blacklist"] == {} or
                        not agent.state.is_blacklisted(agent._fail_key(alt["skill"], {})))

    def test_avoid_repeat_after_three_same(self):
        state, agent = self._agent()
        decision = {"skill": "scan", "args": {"radius": 12}}
        agent._avoid_repeat(decision)          # 1
        agent._avoid_repeat(decision)          # 2
        alt = agent._avoid_repeat(decision)    # 3 → 强制换
        self.assertNotEqual(agent._fail_key(alt["skill"], alt.get("args")),
                            agent._fail_key("scan", {"radius": 12}))

    def test_transient_error_is_not_blacklisted(self):
        state, agent = self._agent()
        with mock.patch("agent.skills.run_skill",
                        side_effect=skills.SkillError("timeout", "慢")), \
                mock.patch.object(agent, "_sleep", return_value=None):
            agent.execute({"skill": "chop_tree", "args": {}})
        key = agent._fail_key("chop_tree", {})
        self.assertFalse(state.is_blacklisted(key))
        self.assertNotIn(key, state.data["failures"])

    def test_fatal_llm_error_stops(self):
        state, agent = self._agent()

        class _FatalLLM:
            usage = {"input": 0, "output": 0, "calls": 0}

            def remaining_input(self):
                return 1000

            def chat_json(self, messages, **kwargs):
                raise LLMFatalError("HTTP 402：账户余额不足")

            def usage_text(self):
                return ""

        agent.llm = _FatalLLM()
        agent.planner.llm = agent.llm      # Planner 用的是自己持有的 llm 引用
        decision = agent.decide()
        self.assertTrue(agent.stop)
        self.assertEqual(decision["skill"], "__finish__")

    def test_guard_reacts_to_player_hurt(self):
        state, agent = self._agent()
        agent._last_hurt_at = time.time()
        agent._last_hurt_by = "Steve"
        agent._last_hurt_is_player = True
        with mock.patch.object(agent, "_run_safety") as run:
            self.assertTrue(agent.guard())
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0], "flee")

    def test_guard_ignores_environment_hurt(self):
        state, agent = self._agent()
        agent._last_hurt_at = time.time()
        agent._last_hurt_by = None
        with mock.patch.object(agent.api, "get_entities", return_value={"entities": []}), \
                mock.patch.object(agent, "_run_safety") as run:
            self.assertFalse(agent.guard())
        run.assert_not_called()

    def test_local_policy_mode(self):
        state, agent = self._agent()
        agent.decide_locally = True
        decision = agent.decide()
        self.assertIn(decision["skill"], skills.SKILLS)

    def test_observe_records_status_latency(self):
        state, agent = self._agent()
        with redirect_stdout(io.StringIO()):
            agent.observe()
        self.assertEqual(agent.status_stats.summary()["n"], 1)

    def test_interaction_latency_callbacks(self):
        state, agent = self._agent()
        agent.cfg.PRINT_LATENCY = False
        agent._on_api_request("GET", "status", 123.4, True)
        agent._on_llm_call(800.0, True)
        self.assertEqual(agent.api_call_stats.summary()["n"], 1)
        self.assertEqual(agent.llm_stats.summary()["n"], 1)


class TestLatency(unittest.TestCase):
    def test_stats_summary(self):
        stats = LatencyStats()
        stats.record(100)
        stats.record(300)
        stats.record_error()
        summary = stats.summary()
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["avg_ms"], 200)
        self.assertGreaterEqual(summary["p95_ms"], 200)
        self.assertIn("avg", stats.text())

    def test_empty_stats(self):
        summary = LatencyStats().summary()
        self.assertEqual(summary["n"], 0)
        self.assertIsNone(summary["avg_ms"])
        self.assertIn("无样本", LatencyStats().text())


class TestBackendSwitch(unittest.TestCase):
    def test_backend_info(self):
        import main as main_module
        with mock.patch.object(main_module.config, "BACKEND", "nmo"):
            backend, desc = main_module.backend_info()
        self.assertEqual(backend, "nmo")
        self.assertIn("NMO", desc)

        with mock.patch.object(main_module.config, "BACKEND", "my_server"), \
                mock.patch.object(main_module.config, "MY_SERVER_BASE", "http://127.0.0.1:8099"):
            backend, desc = main_module.backend_info()
        self.assertEqual(backend, "my_server")
        self.assertIn("127.0.0.1:8099", desc)

    def test_default_backend_is_nmo(self):
        import main as main_module
        with mock.patch.object(main_module.config, "BACKEND", "nmo"):
            self.assertIsInstance(main_module.build_backend(), McApi)

    def test_my_server_backend(self):
        import main as main_module
        with mock.patch.object(main_module.config, "BACKEND", "my_server"), \
                mock.patch.object(main_module.config, "MY_SERVER_BASE", "http://127.0.0.1:8099"), \
                mock.patch.object(main_module.config, "MY_SERVER_TOKEN", "dev"), \
                mock.patch.object(main_module.config, "MY_SERVER_TIMEOUT", 5):
            api = main_module.build_backend()
        self.assertIsInstance(api, MyServerApi)
        self.assertEqual(api.base_url, "http://127.0.0.1:8099")


class TestStrategy(unittest.TestCase):
    def test_default_is_not_preset(self):
        s = strategy_mod.Strategy(os.path.join(tempfile.gettempdir(), "no_such_strategy.json"))
        data = s.load(force=True)
        self.assertEqual(data["mode"], "custom")
        self.assertEqual(data["phases"], [])
        self.assertEqual(s.phase(30)["focus"], "custom")
        self.assertEqual(s.phase(999)["name"], "未设阶段")

    def test_file_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "strategy.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "mode": "nether_rush",
                    "risk": "high",
                    "phases": [{"name": "冲", "until_min": 120, "focus": "nether"}],
                    "avoid": ["minecraft:end/elytra"],
                    "targets": ["minecraft:nether/obtain_blaze_rod"],
                }, f)
            s = strategy_mod.Strategy(path)
            data = s.load(force=True)
            self.assertEqual(data["mode"], "nether_rush")
            self.assertEqual(s.phase(60)["name"], "冲")
            self.assertIn("minecraft:end/elytra", s.avoid_ids())
            self.assertIn("phase=冲", s.summary(10))

    def test_broken_file_keeps_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "strategy.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"mode": "safe_bootstrap"}, f)
            s = strategy_mod.Strategy(path)
            s.load(force=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("{坏掉的 json")
            data = s.load(force=True)
            self.assertEqual(data["mode"], "safe_bootstrap")


class TestKnowledge(unittest.TestCase):
    def test_add_reload_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "knowledge.json")
            md = os.path.join(tmp, "knowledge.md")
            kb = knowledge_mod.KnowledgeBase(path, md)
            kb.add_rule("木材", "橡木原木可以合成 4 个橡木木板")
            kb.add_qa("先学什么？", "先学基础合成")
            kb.add_experiment("scan", {}, True, "看到一棵橡树", {"oak_log": 1})

            again = knowledge_mod.KnowledgeBase(path, md)
            self.assertEqual(again.counts()["rules"], 1)
            self.assertIn("橡木原木", again.summary())
            out = again.export_markdown(md)
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("木材", text)
            self.assertIn("先学基础合成", text)

    def test_episode_context_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "knowledge.json")
            md = os.path.join(tmp, "knowledge.md")
            kb = knowledge_mod.KnowledgeBase(path, md)
            ep = kb.start_episode({"pos": [0, 64, 0], "events": ["受伤 -2"]})
            kb.add_turn(ep["id"], {"kind": "ask", "question": "先学什么？", "answer": "木头"})
            kb.add_turn(ep["id"], {"kind": "experiment", "skill": "scan",
                                   "args": {}, "ok": True, "summary": "看到树"})
            kb.close_episode(ep["id"], "先找树")
            again = knowledge_mod.KnowledgeBase(path, md)
            self.assertEqual(again.counts()["episodes"], 1)
            self.assertIn("最近情境", again.summary())
            again.export_markdown(md)
            with open(md, encoding="utf-8") as fh:
                self.assertIn("学习情境", fh.read())


class TestStrategyDoc(unittest.TestCase):
    def test_markdown_is_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "strategy.md")
            with open(md, "w", encoding="utf-8") as fh:
                fh.write("先挖铁，再做铁镐；不要打袭击。")
            strat = strategy_mod.Strategy(os.path.join(tmp, "none.json"), doc_path=md)
            self.assertTrue(strat.has_doc())
            brief = strat.brief(0)
            self.assertEqual(brief["source"], "strategy.md")
            self.assertIn("先挖铁", brief["text"])


class TestLearnAgent(unittest.TestCase):
    def test_ask_and_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = WorldState(os.path.join(tmp, "s.json")).load()
            kb = knowledge_mod.KnowledgeBase(os.path.join(tmp, "k.json"),
                                             os.path.join(tmp, "k.md"))
            agent = learn_mod.LearnAgent(
                MockApi(), SimpleNamespace(usage_text=lambda: "", usage={}),
                state, kb, input_fn=lambda _prompt: "先学木头", traj=mock.Mock())
            with redirect_stdout(io.StringIO()):
                agent._execute({"action": "ask_user", "question": "先学什么？", "why": ""})
                agent._execute({"action": "record_rule", "topic": "木材",
                                "rule": "原木→木板", "evidence": "用户"})
                agent._execute({"action": "record_skill", "name": "chop_tree",
                                "notes": "先砍树", "args": {}})
            self.assertEqual(kb.counts()["qa"], 1)
            self.assertEqual(kb.counts()["rules"], 1)
            self.assertEqual(kb.counts()["skills"], 1)

    def test_must_ask_before_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = WorldState(os.path.join(tmp, "s.json")).load()
            kb = knowledge_mod.KnowledgeBase(os.path.join(tmp, "k.json"),
                                             os.path.join(tmp, "k.md"))
            llm = SimpleNamespace(chat_json=lambda *a, **k: {
                "action": "experiment", "skill": "scan", "args": {}})
            agent = learn_mod.LearnAgent(
                MockApi(), llm, state, kb, input_fn=lambda _p: "木头", traj=mock.Mock())
            agent.episode = kb.start_episode({"pos": [0, 64, 0]})
            decision = agent._decide()
            self.assertEqual(decision["action"], "ask_user")


class TestManualTester(unittest.TestCase):
    def test_do_and_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = WorldState(os.path.join(tmp, "s.json")).load()
            kb = knowledge_mod.KnowledgeBase(os.path.join(tmp, "k.json"),
                                             os.path.join(tmp, "k.md"))
            tester = manual_mod.ManualTester(
                MockApi(), state, kb, traj=mock.Mock())
            with redirect_stdout(io.StringIO()):
                tester.handle("do scan {}")
                tester.handle("call status {}")
            self.assertGreaterEqual(kb.counts()["experiments"], 1)


class TestChatBot(unittest.TestCase):
    def test_clip_25(self):
        bot = chat_mod.ChatBot(MockApi(), SimpleNamespace(), max_chars=25)
        self.assertEqual(len(bot.clip("字" * 40)), 25)
        self.assertEqual(bot.clip("  你好\n世界  "), "你好 世界")

    def test_parse_chat(self):
        self.assertEqual(
            chat_mod.ChatBot.parse_chat({"type": "chat", "text": "Steve: hello"}),
            ("Steve", "hello"),
        )
        self.assertEqual(
            chat_mod.ChatBot.parse_chat({"type": "chat", "username": "Alex", "message": "hi"}),
            ("Alex", "hi"),
        )
        self.assertIsNone(chat_mod.ChatBot.parse_chat({"type": "death"}))

    def test_reply_is_short_and_sent(self):
        api = MockApi()
        llm = SimpleNamespace(chat=lambda *a, **k: "字" * 40)
        cfg = SimpleNamespace(CHAT_COOLDOWN_S=0)
        bot = chat_mod.ChatBot(api, llm, max_chars=25, cfg=cfg)
        with mock.patch.object(api, "chat") as send, redirect_stdout(io.StringIO()):
            text = bot.reply("Steve", "你好")
        self.assertEqual(len(text), 25)
        send.assert_called_once_with(text)


class TestHotkeys(unittest.TestCase):
    def test_pause_and_emergency(self):
        api = MockApi()
        control = hotkeys_mod.HotkeyController(api)
        with mock.patch.object(api, "stop") as stop:
            control.toggle_pause()
            self.assertTrue(control.paused)
            stop.assert_called_once()
            control.toggle_pause()
            self.assertFalse(control.paused)
        with mock.patch.object(api, "stop") as stop, \
                mock.patch.object(api, "disconnect") as disconnect, \
                mock.patch.object(api, "logout") as logout:
            control.emergency()
            self.assertTrue(control.stop)
            stop.assert_called_once()
            disconnect.assert_called_once()
            logout.assert_called_once()


class TestPlannerStrategy(unittest.TestCase):
    def test_avoid_filtered_from_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "strategy.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"avoid": ["minecraft:bad/thing"]}, f)
            state = WorldState(os.path.join(tmp, "s.json")).load()
            state.data["pending"] = [
                {"id": "minecraft:bad/thing", "score": 100, "title": "坏"},
                {"id": "minecraft:good/thing", "score": 5, "title": "好"},
            ]
            llm = _CapturingLLM({"skill": "scan", "args": {}, "target": "minecraft:bad/thing"})
            decision = Planner(llm, strategy_path=path).decide(state, elapsed_min=10)
            brief = json.loads(llm.messages[1]["content"])
            self.assertEqual([p["id"] for p in brief["pending"]], ["minecraft:good/thing"])
            self.assertEqual(decision["target"], "")

    def test_memory_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            kb = knowledge_mod.KnowledgeBase(os.path.join(tmp, "k.json"),
                                             os.path.join(tmp, "k.md"))
            kb.add_rule("木材", "橡木原木可以合成 4 个橡木木板")
            state = WorldState(os.path.join(tmp, "s.json")).load()
            llm = _CapturingLLM({"skill": "scan", "args": {}})
            planner = Planner(llm, strategy_path=os.path.join(tmp, "none.json"))
            planner.knowledge = kb
            planner.decide(state, elapsed_min=1)
            brief = json.loads(llm.messages[1]["content"])
            self.assertIn("橡木原木", brief["memory"])


class TestReport(unittest.TestCase):
    def test_summarize_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "run-test.jsonl")
            records = [
                {"t": 100.0, "kind": "decision", "skill": "chop_tree"},
                {"t": 101.0, "kind": "skill", "skill": "chop_tree", "ok": True, "ms": 1000},
                {"t": 102.0, "kind": "skill", "skill": "craft", "ok": False, "ms": 500},
                {"t": 103.0, "kind": "advancement", "id": "minecraft:story/mine_stone"},
                {"t": 103.5, "kind": "latency", "target": "llm", "ms": 800},
                {"t": 104.0, "kind": "finish", "reason": "到点", "score": 10,
                 "usage": {"input": 100, "output": 20, "calls": 3}},
            ]
            with open(path, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
            stats = summarize(path)
            self.assertEqual(stats["decisions"], 1)
            self.assertEqual(stats["skills"]["chop_tree"]["ok"], 1)
            self.assertEqual(stats["skills"]["craft"]["fail"], 1)
            self.assertEqual(len(stats["advancements"]), 1)
            self.assertEqual(stats["score"], 10)
            self.assertEqual(stats["latency"]["llm"], [800.0])
            self.assertIn("chop_tree", format_report(stats))
            self.assertIn("网络延迟", format_report(stats))


if __name__ == "__main__":
    unittest.main(verbosity=2)

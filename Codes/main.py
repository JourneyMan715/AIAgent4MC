# -*- coding: utf-8 -*-
"""
Minecraft AI 智能体 —— 入口（Harness 赛道版）

常用命令（先 `cd Codes`，再激活虚拟环境）：

    python main.py run          自主跑（比赛模式：advancement 驱动、无人值守）
    python main.py learn        学习层（学规则/写 memory/有疑问问用户）
    python main.py chat         和 Bot 聊天（LLM 回复，每条 ≤25 字）
    python main.py run --until 18:00
    python main.py run --minutes 30       只跑 30 分钟（联调）
    python main.py run --no-llm           不调用模型，只用本地规则（没余额/局域网测试）
    python main.py run                  运行中按 q 暂停/继续，按 e 强制下线
    python main.py test-api     测大模型连通性（会花一点点 token）
    python main.py test-mc      测比赛服 Bot：登录 + 等上线 + 打印状态
    python main.py status       看 Bot 当前状态
    python main.py strategy     看当前策略（改 data/strategy.json 即换打法）
    python main.py report       看最近一次运行的统计（对比策略用）
    python main.py ping         测网络延迟（DNS/TCP/TLS，不花 token）
    python main.py mock         离线自检（不联网、不花 token）
    python main.py reset        清空本地状态（新开一局前用）

说明：
- 密钥/账号在 `Codes/.env`，不在这个文件里。
- 比赛必须用 AIA 统一代理（改 .env 里的 LLM_* 三行即可）。
- 自己的服务器适配器在 `Codes/my_server/`：在 `.env` 里设 `BACKEND=my_server` 即可切换；
  局域网测试见 `Workflow/局域网测试指南.md`。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config
from agent import Agent
from llm import LLMClient, LLMError
from mc_api import McApi, McApiError
from world_state import WorldState


# ---------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------
def setup_console():
    """Windows 控制台默认不是 UTF-8，中文/emoji 容易乱码。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def state_path(name="world_state.json"):
    return os.path.join(_HERE, config.STATE_DIR, name)


def build_llm(state=None):
    return LLMClient.from_config(config, on_usage=state.update_tokens if state else None)


def build_backend():
    """构造「手脚」。默认用比赛服的 NMO 接口；BACKEND=my_server 时用自有服务器适配器。"""
    backend = str(getattr(config, "BACKEND", "nmo")).strip().lower()

    if backend == "my_server":
        from my_server.adapter import MyServerApi
        return MyServerApi(
            config.MY_SERVER_BASE,
            config.MY_SERVER_TOKEN,
            timeout=config.MY_SERVER_TIMEOUT,
        )

    api = McApi(
        config.MC_API_BASE,
        config.MC_USERNAME,
        config.MC_PASSWORD,
        timeout=config.MC_TIMEOUT,
    )

    # =================================================================
    # 也可以不用 BACKEND，直接把下面两行注释打开（手动切换后端）：
    # from my_server.adapter import MyServerApi
    # api = MyServerApi(config.MY_SERVER_BASE, config.MY_SERVER_TOKEN,
    #                   timeout=config.MY_SERVER_TIMEOUT)
    # =================================================================
    return api


def backend_info():
    """返回 (backend, 人类可读说明)，用于启动时明确「现在连的是哪边」。"""
    backend = str(getattr(config, "BACKEND", "nmo")).strip().lower()
    if backend == "my_server":
        target = config.MY_SERVER_BASE or "(未配置 MY_SERVER_BASE)"
        return backend, f"本地/自有服务器 → {target}"
    return backend, f"比赛方 NMO 接口 → {config.MC_API_BASE}"


def login(api, quiet=False):
    backend = str(getattr(config, "BACKEND", "nmo")).strip().lower()
    if backend == "nmo" and (not config.MC_USERNAME or not config.MC_PASSWORD):
        print("❌ 没有配置 Bot 账号：请在 Codes/.env 里填 MC_USERNAME / MC_PASSWORD")
        return False
    try:
        info = api.login() or {}
    except McApiError as e:
        print(f"❌ 登录失败：{e.code} {e.message}")
        if e.code == "invalid_credentials":
            print("   检查 .env 里的 MC_USERNAME / MC_PASSWORD")
        elif e.code == "no_profile":
            print("   去 https://skin.nmo.net.cn/ 给账号选定一个角色")
        elif e.code == "rate_limited":
            print("   登录被限流，等几十秒再试")
        return False
    if not quiet:
        name = info.get("bot_username") or ("本地 Bot" if backend == "my_server" else "?")
        print(f"✅ 登录成功，Bot 角色：{name}")
    return True


def wait_ready(api, timeout=150):
    print(f"等待 Bot 连进比赛服（最多 {timeout} 秒）……")
    started = time.time()
    tried_connect = False
    while time.time() - started < timeout:
        try:
            status = api.status() or {}
        except McApiError as e:
            if e.code in ("invalid_token", "unauthorized"):
                print("   token 失效，重新登录……")
                if not login(api):
                    return False
            else:
                print(f"   尚未就绪（{e.code}），继续等……")
            time.sleep(2)
            continue

        if status.get("ready"):
            print("✅ Bot 已在比赛服，可以开始")
            return True

        if not tried_connect:
            try:
                api.connect()
            except McApiError:
                pass
            tried_connect = True
        time.sleep(2)

    print("❌ 等待超时：Bot 还没进比赛服")
    return False


def fmt_status(status):
    if not isinstance(status, dict):
        return "（没有状态）"
    pos = status.get("pos") or [0, 0, 0]
    parts = [
        f"坐标 ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})",
        f"血量 {status.get('health')}/20",
        f"饥饿 {status.get('food')}/20",
        f"维度 {status.get('dimension')}",
        "白天" if status.get("is_day") else "黑夜",
    ]
    held = status.get("held")
    if held:
        parts.append(f"手持 {held.get('name')}×{held.get('count', 1)}")
    hostile = status.get("nearest_hostile")
    if hostile:
        parts.append(f"附近 {hostile.get('name')}（{hostile.get('dist')} 格）")
    parts.append(f"ready={status.get('ready')}")
    return "，".join(parts)


# ---------------------------------------------------------------------
# 模式：run（比赛）
# ---------------------------------------------------------------------
def compute_deadline(args):
    if args.minutes:
        return time.time() + args.minutes * 60
    if config.RUN_MINUTES:
        return time.time() + config.RUN_MINUTES * 60
    until = args.until or config.COMPETITION_END
    try:
        hour, minute = [int(x) for x in str(until).split(":")[:2]]
    except (ValueError, TypeError):
        print(f"⚠️ 结束时间「{until}」看不懂（要 HH:MM），本次不设截止")
        return None
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        print(f"⚠️ 结束时间 {until} 今天已经过了，本次不设截止（Ctrl+C 收工）")
        return None
    return target.timestamp()


def cmd_run(args):
    if getattr(args, "strategy", None):
        if str(args.strategy).lower().endswith(".md"):
            config.STRATEGY_DOC = args.strategy
        else:
            config.STRATEGY_FILE = args.strategy
    backend, desc = backend_info()
    print(f"🖥 后端：{desc}")
    api = build_backend()
    if not login(api):
        return 1
    if not wait_ready(api):
        return 1

    state = WorldState(state_path()).load()
    if args.reset:
        print("🧹 已按 --reset 清空本地状态，新开一局")
        state.reset()

    llm = build_llm(state)
    from hotkeys import HotkeyController

    control = HotkeyController(api, allow_msvcrt=True, force_exit=False)
    control.start()
    agent = Agent(api, llm, state, decide_locally=bool(getattr(args, "no_llm", False)),
                  control=control)
    try:
        agent.run(deadline=compute_deadline(args), max_ticks=args.ticks or config.MAX_TICKS)
    finally:
        control.shutdown()
    return 0


# ---------------------------------------------------------------------
# 模式：test-api / test-mc / status / mock / reset
# ---------------------------------------------------------------------
def cmd_test_api(_args):
    llm = build_llm()
    print(f"模型：{config.LLM_MODEL}")
    print(f"地址：{config.LLM_BASE_URL}")
    print("发一条测试消息……")
    try:
        reply = llm.chat(
            [{"role": "user", "content": "只回复四个字：连接成功"}],
            max_tokens=20,
        )
    except LLMError as e:
        print(f"❌ 失败：{e}")
        return 1
    print(f"✅ 模型回复：{reply.strip()}")
    print(f"🪙 {llm.usage_text()}")
    return 0


def cmd_test_mc(_args):
    backend, desc = backend_info()
    print(f"🖥 后端：{desc}")
    api = build_backend()
    if not login(api):
        return 1
    if not wait_ready(api):
        return 1
    try:
        print("当前状态：", fmt_status(api.status()))
        inv = api.inventory() or {}
        items = inv.get("items") or []
        print("背包：" + ("、".join(f"{i['name']}×{i['count']}" for i in items[:12]) or "空"))
        adv = api.advancements(include_recipes=False) or {}
        completed = [a for a in (adv.get("advancements") or []) if a.get("status") == "completed"]
        print(f"进度：已下发 {len(adv.get('advancements') or [])} 项，其中完成 {len(completed)} 项")
        events = api.events(limit=5) or {}
        print(f"最近事件：{[e.get('type') for e in (events.get('events') or [])] or '无'}")
    except McApiError as e:
        print(f"❌ 读取状态失败：{e.code} {e.message}")
        return 1
    print("\n（测试完 Bot 仍在线；要下线可以另跑 python main.py run 后 Ctrl+C，或调用 /disconnect）")
    return 0


def cmd_status(_args):
    backend, desc = backend_info()
    print(f"🖥 后端：{desc}")
    api = build_backend()
    if not login(api, quiet=True):
        return 1
    try:
        print(fmt_status(api.status()))
        inv = api.inventory() or {}
        items = inv.get("items") or []
        print("背包：" + ("、".join(f"{i['name']}×{i['count']}" for i in items[:20]) or "空"))
        print(f"空槽位：{inv.get('empty_slots')}")
    except McApiError as e:
        print(f"❌ {e.code} {e.message}")
        return 1
    return 0


def cmd_mock(args):
    """离线自检：假服务器 + 假大脑，跑 N 轮，看技能和主循环是否正常。"""
    from mock_api import MockApi, ScriptedLLM

    if getattr(args, "strategy", None):
        config.STRATEGY_FILE = args.strategy

    print("=" * 58)
    print(" 离线自检（mock）：不联网、不花 token")
    print("=" * 58)

    api = MockApi()
    state = WorldState(state_path("mock_state.json")).load()
    state.reset()
    llm = ScriptedLLM()

    old_log = config.TASK_LOG_FILE
    old_root = config.TASK_LOG_AT_ROOT
    config.TASK_LOG_FILE = os.path.join(config.LOG_DIR, "任务记录(mock).md")
    config.TASK_LOG_AT_ROOT = False
    try:
        agent = Agent(api, llm, state)
        ticks = args.ticks or 12
        budget = max(60, ticks * 8)
        agent.run(deadline=time.time() + budget, max_ticks=ticks)
    finally:
        config.TASK_LOG_FILE = old_log
        config.TASK_LOG_AT_ROOT = old_root

    print("\n最终背包：", api.inventory().get("items"))
    print("已触发进度：", list(api.completed))
    print("自检完成 ✅")
    return 0


def cmd_reset(_args):
    state = WorldState(state_path()).load()
    state.reset()
    print(f"✅ 已清空本地状态：{state.path}")
    return 0


def cmd_strategy(args):
    from strategy import Strategy

    explicit = args.file
    if explicit and str(explicit).lower().endswith(".md"):
        strat = Strategy(doc_path=explicit)
        doc = strat.doc_text()
        if not doc:
            print(f"❌ 策略文件为空或不存在：{explicit}")
            return 1
        print(f"策略文件：{strat.doc_path}（自由文本，{len(doc)} 字）")
        print("-" * 56)
        print(doc)
        print("-" * 56)
        print("说明：strategy.md 优先；LLM 会自己阅读/拆解，不套预设阶段。")
        return 0

    path = explicit or config.STRATEGY_FILE
    strat = Strategy(path, doc_path=getattr(config, "STRATEGY_DOC", None))
    doc = strat.doc_text()
    if doc and not explicit:
        print(f"策略文件：{strat.doc_path}（自由文本，{len(doc)} 字，优先于 JSON）")
        print("-" * 56)
        print(doc)
        print("-" * 56)
        print("说明：LLM 会自己阅读/拆解；JSON 仅作兼容/备用。")
        return 0

    data = strat.load(force=True)
    print(f"策略文件：{strat.path}")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if doc:
        print(f"\n（另有 strategy.md，{len(doc)} 字；运行时优先用它）")

    # 顺手校验 avoid/targets 里的 id 有没有写错（不在计分表里的会提示）
    try:
        from advancements import load_table
        table = load_table(os.path.join(_HERE, "data", "advancement_scores.json"))
        ids = list(data.get("avoid") or []) + list(data.get("targets") or [])
        unknown = [i for i in ids if i and i not in table and not i.endswith("/root")]
        if unknown:
            print("⚠️ 这些 id 不在官方计分表里（可能拼错，或本来就不计分）：")
            for item in unknown:
                print("   -", item)
    except Exception:
        pass

    elapsed = 0.0
    try:
        state = WorldState(state_path()).load()
        started = float(state.data.get("started_at") or 0)
        if started:
            elapsed = max(0.0, (time.time() - started) / 60.0)
    except Exception:
        pass
    phase = strat.phase(elapsed)
    print(f"\n当前（本地已运行 {elapsed:.0f} 分钟）：阶段={phase.get('name')}，focus={phase.get('focus')}")
    print(f"摘要：{strat.summary(elapsed)}")
    return 0


def cmd_report(args):
    from report import format_report, latest_log, summarize

    path = args.file or latest_log(os.path.join(_HERE, config.LOG_DIR))
    if not path:
        print("还没有轨迹文件：先跑一次 `python main.py run` 或 `mock`")
        return 1
    stats = summarize(path)
    if not stats:
        print(f"读不了：{path}")
        return 1
    print(format_report(stats))
    return 0


def cmd_ping(args):
    from netcheck import format_ping, ping_url

    if args.url:
        targets = [("自定义", args.url)]
    else:
        targets = [
            ("大模型", config.LLM_BASE_URL),
            ("比赛服 API", config.MC_API_BASE),
        ]
        if config.MY_SERVER_BASE:
            targets.append(("自有/本地桥接", config.MY_SERVER_BASE))

    samples = max(1, int(args.samples or 5))
    backend, desc = backend_info()
    print(f"当前后端（实际动作走这里）：{desc}")
    print(f"采样 {samples} 次（只做 DNS/TCP/TLS 握手，不调用业务接口、不消耗 token）\n")
    for name, url in targets:
        if not url:
            print(f"{name}：未配置，跳过\n")
            continue
        try:
            result = ping_url(url, samples=samples)
        except Exception as exc:
            print(f"{name}：测量失败（{exc}）\n")
            continue
        print(format_ping(name, result))
        print()
    print("参考：<200ms 很好；200~500ms 正常；>800ms 偏慢；>3000ms 容易超时")
    return 0


def cmd_learn(args):
    """学习层：学规则、问用户、写 memory；不冲分、不读 strategy。"""
    from knowledge import KnowledgeBase
    from learn import LearnAgent

    backend, desc = backend_info()
    print(f"🖥 后端：{desc}")
    print("🧠 学习层：目标是把游戏规则学明白（不是比赛执行）")
    api = build_backend()
    if not login(api):
        return 1
    if not wait_ready(api):
        return 1

    state = WorldState(state_path()).load()
    if args.reset:
        print("🧹 已清空本地世界状态")
        state.reset()

    knowledge = KnowledgeBase()
    if args.reset_knowledge:
        knowledge.reset()
        print("🧹 已清空学习记忆（knowledge.json/md）")

    if getattr(args, "manual", False):
        from hotkeys import HotkeyController
        from manual import ManualTester

        control = HotkeyController(api, allow_msvcrt=True, force_exit=True)
        control.start()
        try:
            tester = ManualTester(api, state, knowledge, control=control)
            tester.run()
        finally:
            control.shutdown()
        return 0

    from hotkeys import HotkeyController

    control = HotkeyController(api, allow_msvcrt=True, force_exit=True)
    control.start()
    try:
        llm = build_llm(state)
        learner = LearnAgent(api, llm, state, knowledge, control=control)
        learner.run(deadline=compute_deadline(args),
                    max_ticks=args.ticks or config.LEARN_MAX_TICKS)
    finally:
        control.shutdown()
    return 0


def cmd_chat(args):
    """和 Bot 聊天：终端/游戏内消息 → LLM → 25 字内回复。"""
    from chat import ChatBot

    backend, desc = backend_info()
    print(f"🖥 后端：{desc}")
    api = build_backend()
    if not login(api):
        return 1
    if not wait_ready(api):
        return 1
    state = WorldState(state_path()).load()
    llm = build_llm(state)
    from hotkeys import HotkeyController

    control = HotkeyController(api, allow_msvcrt=True, force_exit=True)
    control.start()
    try:
        bot = ChatBot(api, llm, control=control)
        bot.run(deadline=compute_deadline(args))
    finally:
        control.shutdown()
    return 0


def cmd_help(_args=None):
    print(__doc__)
    print("提示：先 `cd Codes`，激活虚拟环境（venv\\Scripts\\activate），再运行上面的命令。")
    return 0


# ---------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Minecraft AI 智能体（Harness 赛道）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode")

    run_p = sub.add_parser("run", help="自主运行（比赛模式）")
    run_p.add_argument("--until", default=None, help="结束时间，形如 18:00")
    run_p.add_argument("--minutes", type=int, default=0, help="只跑多少分钟（联调用）")
    run_p.add_argument("--ticks", type=int, default=0, help="最多决策多少轮（默认用配置）")
    run_p.add_argument("--reset", action="store_true", help="先清空本地状态（新开一局）")
    run_p.add_argument("--strategy", default=None,
                       help="策略文件（.md 自由文本优先；.json 兼容旧格式）")
    run_p.add_argument("--no-llm", action="store_true",
                       help="不调用大模型，只用本地规则（局域网测试/没余额时用）")
    run_p.set_defaults(func=cmd_run)

    learn_p = sub.add_parser("learn", help="学习层：学规则/写 memory/有疑问问用户")
    learn_p.add_argument("--until", default=None, help="结束时间，形如 18:00")
    learn_p.add_argument("--minutes", type=int, default=0, help="只学多少分钟")
    learn_p.add_argument("--ticks", type=int, default=0,
                         help="最多学习多少轮（默认用 LEARN_MAX_TICKS）")
    learn_p.add_argument("--reset", action="store_true", help="先清空本地世界状态")
    learn_p.add_argument("--reset-knowledge", action="store_true",
                         help="先清空学习记忆（knowledge.json/md）")
    learn_p.add_argument("--manual", action="store_true",
                         help="手动动作测试（不调用模型，输入 do/call 直接试）")
    learn_p.set_defaults(func=cmd_learn)

    chat_p = sub.add_parser("chat", help="和 Bot 聊天（LLM 回复，每条 ≤25 字）")
    chat_p.add_argument("--until", default=None, help="结束时间，形如 18:00")
    chat_p.add_argument("--minutes", type=int, default=0, help="只聊多少分钟")
    chat_p.set_defaults(func=cmd_chat)

    sub.add_parser("test-api", help="测大模型连通性").set_defaults(func=cmd_test_api)
    sub.add_parser("test-mc", help="测比赛服 Bot").set_defaults(func=cmd_test_mc)
    sub.add_parser("status", help="看 Bot 状态").set_defaults(func=cmd_status)

    strat_p = sub.add_parser("strategy", help="查看当前策略")
    strat_p.add_argument("--file", default=None, help="策略文件路径")
    strat_p.set_defaults(func=cmd_strategy)

    report_p = sub.add_parser("report", help="看最近一次运行的统计")
    report_p.add_argument("--file", default=None, help="指定轨迹 jsonl")
    report_p.set_defaults(func=cmd_report)

    ping_p = sub.add_parser("ping", help="测网络延迟（DNS/TCP/TLS）")
    ping_p.add_argument("--samples", type=int, default=5, help="采样次数（默认 5）")
    ping_p.add_argument("--url", default=None, help="只测指定 URL")
    ping_p.set_defaults(func=cmd_ping)

    mock_p = sub.add_parser("mock", help="离线自检（不联网）")
    mock_p.add_argument("--ticks", type=int, default=12, help="跑多少轮")
    mock_p.add_argument("--strategy", default=None, help="策略文件（可选）")
    mock_p.set_defaults(func=cmd_mock)

    sub.add_parser("reset", help="清空本地状态").set_defaults(func=cmd_reset)
    sub.add_parser("help", help="看帮助").set_defaults(func=cmd_help)

    return parser


def main(argv=None):
    setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "mode", None):
        cmd_help()
        return 0

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在退出（状态已保存，下次可接着跑）")
        return 130


if __name__ == "__main__":
    sys.exit(main())

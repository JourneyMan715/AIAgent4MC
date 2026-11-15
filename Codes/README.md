# Codes —— 代码目录

> 工程总手册（介绍/架构/依赖/测试/命令/策略/规则）在项目根 [`README.md`](../README.md)；
> 本文只做代码目录速查。

> 这里是「大脑 + 手脚 + 记忆」的全部实现。想先跑起来，看下面 5 条命令就行。

## 快速开始

```bat
cd Codes
venv\Scripts\activate
python main.py test-api      :: 测大模型
python main.py test-mc       :: 测当前后端 Bot
python main.py learn --minutes 20  :: 学习层：学规则/写 memory（会问你）
python main.py chat           :: 和 Bot 聊天（LLM，≤25 字/条）
python main.py run           :: 决策层：按 strategy 打球
```

离线自检（不联网、不花 token）：

```bat
python main.py mock --ticks 12
```

看策略 / 看最近一次运行统计：

```bat
python main.py strategy
python main.py report
python main.py ping --samples 5
```

## 文件地图

| 文件 | 干什么 |
|---|---|
| `main.py` | 入口：`run / learn / test-api / test-mc / status / strategy / report / ping / mock / reset` |
| `manual.py` | 学习层的手动动作测试（`learn --manual`：`do`/`call`） |
| `chat.py` | 聊天模式（`chat`：终端/游戏内消息 → LLM → ≤25 字回复） |
| `hotkeys.py` | `run` 热键：`q` 暂停/继续，`e` 强制下线 |
| `config.py` | 所有配置项（从 `.env` 读密钥，本身不含密码） |
| `.env` | **你的密钥和账号**（已被 gitignore，别提交） |
| `llm.py` | 大模型客户端：计量、预算、重试、JSON 解析 |
| `planner.py` | 决策层：把状态 + memory + strategy 发给模型，要回「下一个技能」 |
| `learn.py` | 学习层：实验 / 问用户 / 写 memory（不冲分） |
| `knowledge.py` | 学习记忆：`state/knowledge.json/md` 的读写与导出 |
| `skills.py` | 技能层：勘察 / 砍树 / 采集 / 合成 / 熔炼 / 吃 / 战斗 / 逃 / 探索… |
| `agent.py` | 主循环：观察 → 保命 → 决策 → 执行 → 记账（无人值守） |
| `world_state.py` | 世界状态 + 记忆：位置、背包、进度、路点、失败、token（落盘） |
| `advancements.py` | advancement 计分（只看难度：5/15/30/60/100；不计分项 0 分） |
| `strategy.py` | 策略加载（`strategy.md` 优先，JSON 兼容，支持热更新） |
| `data/strategy.md` | **你写的自由策略**（LLM 自己拆解；推荐） |
| `data/strategy.example.md` | 策略模板（复制成 strategy.md） |
| `data/strategy.json` | 兼容策略（默认空，不预设阶段） |
| `data/strategy_nether.json` / `strategy_safe.json` | 两套示例策略（下界冲刺 / 保底） |
| `report.py` | 轨迹统计（对比策略用） |
| `data/advancement_scores.json` | 官方计分表（由 `Doc/RULES.md` 自动生成，99 项 / 3388 分） |
| `build_scores_table.py` | 规则更新后重新生成上面的计分表 |
| `netcheck.py` | 网络延迟测量（DNS/TCP/TLS + 运行统计）；每条 API/LLM 交互都会打印耗时 |
| `scan_sensitive.py` | **开原前隐私自查**（只输出掩码；`--all` 连本机文件一起扫） |
| `trajectory.py` | 轨迹日志（JSONL）+ 人类可读总结（任务记录.md） |
| `mc_api.py` | 比赛服 NMO 接口客户端（规范 v1.3 全端点 + `stop_on`） |
| `my_server/` | **以后接自己的服务器**用的适配器（`BACKEND=my_server` 切换） |
| `local_bot/` | **局域网测试**用的 Mineflayer 桥接（Node.js，另开一个进程） |
| `mock_api.py` | 离线假服务器 + 假大脑（自检 / 单测用） |
| `supervisor.py` | 守护进程：崩溃自动拉起，跑到结束时间 |
| `tests/` | 88 个离线单元测试 |
| `requirements.txt` | 依赖（只有 `requests`） |

## 一圈（tick）在干什么

```
读状态 /status            ┐
读背包 /inventory          ├─ 观察（≤4 并发额度内，尽量串行少请求）
读事件 /events             │
刷新进度 /advancements     ┘
        ↓
保命检查：吃 / 逃 / 死亡循环避让
        ↓
问大脑一次（约 1.5k 字符上下文）→ 得到 {"skill": ..., "args": ...}
        ↓
执行技能（内部可能几十个 API 动作）
        ↓
更新状态 + 写轨迹 + 落盘
```

## 三个关键设计

1. **技能层**：模型不需要「挖一个方块问一次」，它只说 `chop_tree`，代码自己找树、走到树下、自下而上挖、确认背包增量。
2. **token 预算**：每次决策只发一小段结构化状态，不重发聊天历史；到 70% 提示、98% 主动收手。
3. **可恢复**：状态存在 `state/world_state.json`，崩溃重启不失忆；每一步写 `logs/run-*.jsonl` 便于复盘。

## 测试

```bat
python -m unittest discover -s tests -v
```

改完技能先跑测试和 `python main.py mock`，再上真服。

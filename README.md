# AIAgent4MC 工程手册

> 一支能自己玩 Minecraft 的 Agent：**大模型做低频规划，代码做高频执行，状态落盘可恢复，token 有预算，每一步可复盘。**
>
> 参赛赛道：南京大学「AI Agent for Minecraft 开发大赛」· **Harness 赛道**
> 当前版本：v3（2026-11-16）· 离线测试：88 项全过 · 新增学习层/自由 strategy
> 规则依据：[`Doc/RULES.md`](Doc/RULES.md)（初赛规则草案 v2）

---

<a id="toc"></a>
## 目录

| # | 章节 | 你会得到什么 |
|---|---|---|
| 0 | [阅读指南](#sec-0) | 不同角色该读哪几节 |
| 1 | [工程介绍](#sec-1) | 这个工程是什么、现在能做什么、目录长什么样 |
| 2 | [依赖与环境](#sec-2) | 装什么、版本要求、账号准备 |
| 3 | [初次测试：从零跑通](#sec-3) | 第一次跑起来的完整命令与预期输出 |
| 4 | [架构设计与运行逻辑](#sec-4) | 分层、一次 tick 的流程、状态/预算/防死循环 |
| 4.11 | [学习层（learn 模式）](#sec-learn) | 先学规则、写 memory、有疑问问你 |
| 4.12 | [配方与合成](#sec-recipe) | 为什么配方会被拒、怎么修 |
| 4.13 | [聊天模式（≤25 字）](#sec-chat) | 用 LLM 和 Bot 说话 |
| 5 | [赛前测试与后续测试](#sec-5) | 三档测试、A/B、浸泡、指标判读 |
| 6 | [命令速查（初次 / 后续）](#sec-6) | 两张表，照着敲 |
| 7 | [策略撰写与规划](#sec-7) | `strategy.md` 自由文本、JSON 兼容、怎么 A/B |
| 8 | [赛场规则（RULES.md v2）](#sec-8) | 计分、配额、违规、赛程，以及规则怎么落到代码里 |
| 9 | [比赛日操作手册](#sec-9) | T-7 / T-1 / 当天 / 应急 / 收尾 |
| 10 | [局域网与自有服务器](#sec-10) | 为什么不能直连、怎么用本地桥接测 |
| 11 | [故障排查 FAQ](#sec-11) | 常见症状 → 原因 → 处理 |
| 12 | [文档索引](#sec-12) | 每份文档讲什么、谁是权威 |
| A | [附录 A：命令与配置速查](#app-a) | 一页纸 |
| B | [附录 B：文件地图](#app-b) | 每个文件干什么 |
| C | [附录 C：术语表](#app-c) | 对齐概念 |
| D | [附录 D：版本与变更](#app-d) | v1 → v2 → 规则 v2 → 防死循环 |
| E | [附录 E：已知限制与路线图](#app-e) | 现在做不到什么、下一步做什么 |

---

<a id="sec-0"></a>
## 0. 阅读指南

| 你的情况 | 建议阅读顺序 |
|---|---|
| 第一次接触这个项目 | 1 → 2 → 3 → 4（先跑起来，再看原理） |
| 赛前准备 / 调策略 | 5 → 6 → 7 → 9 |
| 比赛当天 | 9（照着做）+ 11（出问题查） |
| 想改代码 / 加技能 | 4 → B（文件地图）→ 附录 E |
| 只想看规则和算分 | 8 |
| 想在自己的服务器上跑 | 10 + [`Codes/my_server/README.md`](Codes/my_server/README.md) |

> 本手册是**总入口**，内容自洽；更细的专题在 [`Doc/索引.md`](Doc/索引.md) 里有完整列表与定位。

---

<a id="sec-1"></a>
## 1. 工程介绍

### 1.1 一句话

**DeepSeek（或 AIA 统一代理）当大脑，通过 NMO 的 HTTP 接口控制一个 Minecraft Bot，在 8 小时里自己定目标、自己挖造打、尽量多拿 advancement 分。**

### 1.2 比赛背景（为什么这样设计）

- 赛道：Harness 赛道，考察「规划调度、记忆、工具调用、状态维护、行为策略」；
- 计分：游戏表现 60（advancement 得分归一化）+ 工程评审 40；
- 场地：每人一座相同的**虚空岛**（主世界 + 下界），末地共用，末影龙赛前已被击杀；
- 资源：8 小时、输入 800 万 / 输出 150 万 token、单队并发 ≤ 4、模型必须走 AIA 统一代理。

> 详细规则见 [第 8 章](#sec-8) 或 [`Doc/RULES.md`](Doc/RULES.md)。

### 1.3 现在能做什么（诚实版）

| 能力 | 状态 |
|---|---|
| 基础生存：砍树、采集石头/煤/铁、合成（含递归补料）、熔炼、吃东西、装备 | ✅ 已实现 |
| 战斗：近战追击、逃跑 | ✅ 已实现（弓/弩接口有，技能未封装） |
| 勘察：扫描周围方块/生物、识别岛上结构（村庄/要塞/堡垒/试炼密室/古城…） | ✅ 已实现 |
| 状态维护：背包/坐标/进度/失败/路点落盘，崩溃恢复 | ✅ 已实现 |
| 计分驱动：读 `/advancements`、按官方计分表挑目标、`avoid` 过滤 | ✅ 已实现 |
| 策略配置：`strategy.md` 自由文本（LLM 拆解）/ JSON 兼容，热更新 | ✅ 已实现 |
| 学习层：实验 + 问用户 + 写 memory（规则/技能/问答） | ✅ 已实现（`learn` 模式） |
| 防死循环：拉黑救回、重复检测、无进展检测、402 直接停 | ✅ 已实现 |
| 下界/末地/村庄/试炼**专精技能**（造门、要塞、酿造、附魔、交易、末地城…） | ⏳ 未实现（路线已规划） |
| 任务图调度器（硬流程） | ⏳ 未实现（当前是软约束 + LLM） |
| 局域网桥接的 advancement 统计 | ❌ 不支持（本地测试用，不影响比赛） |

**一句话**：现在是一个**能先学规则、再按你写的 strategy 打球、能在局域网被验证**的 Harness；高分路线（下界/末地）还需要补技能。

### 1.4 目录结构

```
AIAgent4MC/
├─ README.md                 ← 本手册（总入口）
├─ Codes/                    ← 全部代码
│  ├─ main.py                入口（run/learn/test-*/status/strategy/report/mock/reset）
│  ├─ config.py              配置（从 .env 读密钥）
│  ├─ .env                   密钥与账号（gitignore）
│  ├─ llm.py                 大模型客户端（计量/预算/重试/JSON）
│  ├─ planner.py             决策层：读 memory + strategy，要一个技能
│  ├─ learn.py               学习层：实验/提问/写 memory
│  ├─ knowledge.py           学习记忆（knowledge.json/md）
│  ├─ skills.py              技能层（12 个）
│  ├─ agent.py               主循环（无人值守）
│  ├─ world_state.py         世界状态 + 记忆（落盘）
│  ├─ advancements.py        计分
│  ├─ strategy.py            策略加载（strategy.md 优先，JSON 兼容）
│  ├─ data/strategy.md       你的自由策略（推荐；LLM 自己拆）
│  ├─ data/strategy*.json    兼容/示例策略
│  ├─ data/advancement_scores.json  官方计分表（99 项 / 3388 分）
│  ├─ mc_api.py              比赛服接口客户端（全端点 + stop_on）
│  ├─ my_server/             自有服务器适配器（BACKEND=my_server）
│  ├─ local_bot/             局域网测试桥接（Node + Mineflayer）
│  ├─ mock_api.py            离线假服务器（自检/单测）
│  ├─ trajectory.py          轨迹日志
│  ├─ report.py              轨迹统计
│  ├─ supervisor.py          守护进程（崩溃自动拉起）
│  └─ tests/                 88 项离线单测
├─ Doc/
│  ├─ RULES.md                 ← 官方初赛规则（草案 v2，计分表来源）
│  ├─ 参赛要求.md             官方公告 + v2 变更说明（历史存档）
│  ├─ agent-api-spec-dist.md  接口规范 v1.3
│  └─ 索引.md                 文档索引（每份文档的定位）
└─ Workflow/                 专题文档（学习层/配方/复现/教程/策略/比赛日…）
```

### 1.5 设计目标（取舍原则）

1. **token 要省**：一次决策约 1.4k 字符，8 小时约 100 万 token，远低于 800 万；
2. **能跑满 8 小时**：无人值守、崩溃恢复、到点收尾、预算熔断；
3. **可解释、可复盘**：每个决策/动作/失败都写 JSONL，评测和答辩都能讲清楚；
4. **可替换**：换模型只改 `.env`；换服务器只换后端；加技能只改 `skills.py`。

---

<a id="sec-2"></a>
## 2. 依赖与环境

### 2.1 软件依赖

| 依赖 | 版本 | 用途 | 必需 |
|---|---|---|---|
| Python | ≥ 3.10（本机 3.14.8） | 运行 Agent | ✅ |
| `requests` | ≥ 2.31.0 | 调大模型 / NMO 接口 | ✅ |
| Node.js | ≥ 18 | 只用于**局域网桥接** `Codes/local_bot` | 选填 |
| `mineflayer` / `mineflayer-pathfinder` / `mineflayer-collectblock` / `express` / `vec3` | 最新版 | 局域网桥接 | 选填 |

> Python 侧只有 `requests` 一个第三方库（`Codes/requirements.txt`）。
> 单测/自检不需要联网、不需要 Node、不需要 Minecraft。

### 2.2 账号与网络

| 需要什么 | 说明 |
|---|---|
| 大模型 key | 赛前用 DeepSeek 自测；**比赛必须换成 AIA 统一代理**（改 `.env` 三行） |
| 皮肤站账号 | <https://skin.nmo.net.cn/> 注册并选定角色（NMO 接口用它登录 Bot） |
| 网络 | 需要能访问代理地址与 `mc-api.windywindie.top`；局域网桥接只走 `127.0.0.1` |

### 2.3 配置在哪

**密钥只放 `Codes/.env`**（已 gitignore），`config.py` 里没有密码。

```ini
# Codes/.env
LLM_BASE_URL=https://api.deepseek.com/v1   # 比赛换成 AIA 代理
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-...
MC_USERNAME=你的皮肤站用户名
MC_PASSWORD=你的皮肤站密码
BACKEND=nmo                                 # nmo=比赛服；my_server=自有/局域网
MY_SERVER_BASE=                             # 局域网：http://127.0.0.1:8099
MY_SERVER_TOKEN=dev
```

> 🔐 **开源/分享前必读**：所有需要删除或替换的 key、密码、账号、路径、端口，都整理在
> [`Doc/敏感信息清单.md`](Doc/敏感信息清单.md)（含扫描脚本与历史提交处理）。

> 🖥 **先确认连的是哪边**：`run / test-mc / status / ping` 启动都会打印
> `🖥 后端：比赛方 NMO 接口 → …` 或 `🖥 后端：本地/自有服务器 → …`。
> 它由 `Codes/.env` 的 `BACKEND` 决定（`nmo` / `my_server`）。
> 报 `ECONNREFUSED 127.0.0.1:8099` 说明当前是 `my_server` 但本地桥接没启动。

常用可调项在 `Codes/config.py`（改完重启生效）：

| 配置 | 默认 | 含义 |
|---|---|---|
| `INPUT_TOKEN_BUDGET` / `OUTPUT_TOKEN_BUDGET` | 8,000,000 / 1,500,000 | 比赛额度 |
| `COMPETITION_END` | `18:00` | 当天几点收工 |
| `EAT_BELOW_FOOD` | 14 | 饥饿低于多少吃东西 |
| `FLEE_BELOW_HEALTH` | 6 | 血量低于多少逃跑 |
| `FAIL_BLACKLIST_COUNT` / `_SECONDS` | 2 次 / 120 秒 | 业务失败拉黑 |
| `ADVANCEMENT_REFRESH_S` | 90 | 多久刷一次进度 |
| `NET_LATENCY_WARN_MS` | 3000 | 单次延迟告警阈值（毫秒） |
| `PRINT_LATENCY` | `True` | 控制台逐条打印每次交互的延迟（关掉更安静） |
| `STRATEGY_FILE` | `data/strategy.json` | 兼容策略（默认空） |
| `STRATEGY_DOC` | `data/strategy.md` | 自由策略（优先） |
| `LEARN_MAX_TICKS` | 200 | 学习层默认轮数 |
| `LOOP_LIMIT` | 10 | 连续多少轮相同动作/状态判定死循环 |
| `CHAT_MAX_CHARS` | 25 | 聊天回复最大字数 |
| `CHAT_COOLDOWN_S` | 1.2 | 聊天回复冷却（秒） |

---

<a id="sec-3"></a>
## 3. 初次测试：从零跑通

> 目标：不碰比赛服、不烧 token，先确认「环境 → 代码 → 模型 → Bot」四层都通。

### 3.1 装环境

```bat
cd /d C:\你的路径\AIAgent4MC\Codes
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

看到命令行前缀 `(venv)` 即成功。国内可用
`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`。

### 3.2 填 `.env`

```bat
copy .env.example .env
notepad .env
```

至少填 `LLM_API_KEY`、`MC_USERNAME`、`MC_PASSWORD`。
比赛前把 `LLM_*` 换成 AIA 代理的三件套。

### 3.3 离线自检（不联网、不花钱）

```bat
python main.py mock --ticks 12
```

预期（节选）：

```
🧭 决策：chop_tree({}) —— 先弄木头
✅ 砍树 oak_log×4（0s）
...
🏆 本次新完成进度 2 项，累计预计 10 分
自检完成 ✅
```

这一步验证：技能链路、递归合成、状态落盘、轨迹、计分、防死循环。

### 3.4 测大模型

```bat
python main.py test-api
```

预期：

```
✅ 模型回复：连接成功
🪙 输入 13 / 8000000 tokens，输出 4 / 1500000，调用 1 次
```

| 报错 | 原因 | 处理 |
|---|---|---|
| `401` | key 错/过期 | 改 `.env` |
| `402` | **余额不足** | 充值或换代理（程序遇到 402 会直接停，不再空转） |
| `404` | 地址少了 `/v1` | 修正 `LLM_BASE_URL` |
| `network_error` | 网络/代理不通 | 检查网络与地址 |

### 3.5 测 Bot

先测一下网络（不花 token、不需要登录）：

```bat
python main.py ping --samples 5
```

会打印大模型、比赛服 API、本地桥接的 **DNS / TCP / TLS / 总耗时**。
然后再测 Bot：

```bat
python main.py test-mc
```

预期：登录成功 → `Bot 已在比赛服` → 坐标/血量/背包/进度数/最近事件。

| 报错 | 处理 |
|---|---|
| `invalid_credentials` | 核对 `.env` 账号 |
| `no_profile` | 去皮肤站选定角色 |
| 长时间 `ready=false` | 等 20~30 秒；程序会自动重试 |

> 想在自己的世界测，跳转到 [第 10 章](#sec-10)（需要 Node + `local_bot` 桥接）。

### 3.6 第一次短跑

```bat
python main.py reset
python main.py run --minutes 10
```

跑完看：

```bat
python main.py report
```

预期能看到：决策次数、各技能成功率、完成进度、token、死亡次数。

### 3.7 产物在哪

| 产物 | 位置 | 用途 |
|---|---|---|
| 世界状态 | `Codes/state/world_state.json` | 重启恢复；别手改 |
| 轨迹 | `Codes/logs/run-*.jsonl` | 每步决策/动作/错误，复盘用 |
| 运行总结 | `任务记录.md`（项目根） | 人类可读的总结 |

**初次测试通过标准**：`mock` 成功 + `test-api` 成功 + `test-mc`（或 LAN `status`）成功 + 10 分钟 `run` 有决策/动作/日志。

---

<a id="sec-4"></a>
## 4. 架构设计与运行逻辑

### 4.1 总览

```
┌──────────────────────────────────────────────────────────────┐
│ 入口层  main.py                                               │
│  run / test-api / test-mc / status / strategy / report / mock │
└───────────────────────────┬──────────────────────────────────┘
                            │ 登录 + 等 ready + 加载状态
┌───────────────────────────▼──────────────────────────────────┐
│ 编排层  agent.py（无人值守主循环）                             │
│  观察 → 保命 → 决策 → 执行 → 记账 → 落盘                       │
└───────┬───────────────┬───────────────┬──────────────────────┘
        │ 结构化状态     │ 预算/时钟      │ 技能调用
┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼──────────────────────┐
│ planner.py   │ │ llm.py       │ │ skills.py（12 个技能）       │
│ 提示词/校验   │ │ 计量/预算/重试│ │ 砍树/采集/合成/熔炼/吃/…    │
└───────┬──────┘ └──────────────┘ └─────┬──────────────────────┘
        │                              │ HTTP
┌───────▼──────────────┐      ┌────────▼──────────────────────┐
│ strategy.py          │      │ mc_api.py（比赛服）            │
│ 阶段/分支/avoid       │      │ my_server/（自有服务器）        │
└──────────────────────┘      └────────┬──────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────┐
│ world_state.py（状态+记忆）  trajectory.py（轨迹）  report.py │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 一次 tick（约 1~60 秒）做什么

1. **观察**
   - `GET /status`：坐标、血量、饥饿、维度、附近敌对生物；
   - `GET /inventory`：带 6 秒缓存；
   - `POST /events`：受伤/死亡/聊天/掉线；
   - `POST /advancements`：默认 90 秒刷一次（`include_recipes=false`）。
2. **保命**（`agent.guard`）
   - 连续死亡达阈值 → 主动休息，避免服务器强制下线 5 分钟；
   - 饥饿低 + 有食物 → 吃；
   - 血量低 + 有敌对生物 → 逃。
3. **决策**（`planner.decide`）
   - 组一段很短的提示：固定系统提示（技能表/规则/策略）+ 约 250 字符的结构化状态；
   - 模型只回 `{"skill","args","target","why"}`；
   - 失败时用本地兜底（并避开黑名单）；`--no-llm` 时只用本地规则。
4. **执行**（`skills.run_skill`）
   - 技能内部做「定位 → 走过去 → 动作 → 校验 → 重试/降级」；
   - 一次技能可能对应几十个 API 动作。
5. **记账**
   - 写状态、写轨迹、刷新进度（部分技能后强制刷新）、保存。

### 4.3 为什么不是「一个动作问一次模型」

旧架构每做一个 API 动作问一次模型，且每次重发全部历史。按经验：
累计输入 ≈ `1400R + 400R²`，**约 140 轮就把 800 万额度烧完**，而比赛有 8 小时。

现在：

| 指标 | 旧 | 新 |
|---|---|---|
| 一次决策 | 1 个动作 | 一个技能（5~40 个动作） |
| 上下文 | 全量历史，持续增长 | 固定约 1.4k 字符 |
| 8 小时决策数 | 约 140 轮（额度耗尽） | 约 1000 次，约 100 万 token |
| 运行形态 | 每个任务等人输入 | 无人值守，到点收尾 |

### 4.4 状态与记忆（`world_state.py`）

`Codes/state/world_state.json` 关键字段：

| 字段 | 内容 |
|---|---|
| `pos / health / food / inventory` | 当前状态（背包带时间戳，缓存用） |
| `advancements` | 已完成进度 + 分数 |
| `pending` | 候选进度（已剔除 0 分/avoid） |
| `island` | 岛屿勘察结果（结构/方块/群系） |
| `failures / blacklist` | 失败计数与拉黑 |
| `recent / events` | 最近动作与事件（进提示词） |
| `tokens / stats` | token 账本、轮数、死亡时间 |

写入是「先写临时文件再替换」，进程被杀也不会写坏；重启读回接着跑。

### 4.5 技能层（`skills.py`）

| 技能 | 作用 |
|---|---|
| `survey` | 勘察岛屿，识别村庄/要塞/堡垒/试炼密室/古城/珊瑚/雪原等 |
| `scan` | 看周围方块与生物 |
| `chop_tree` | 找树 → 走到树下 → 自下而上挖 → 汇总掉落 |
| `collect` | 按组采集（log/stone/coal/iron/diamond…），确认背包增量 |
| `craft` | 合成，缺料递归补、缺工作台自动放 |
| `smelt` | 放燃料/原料 → 等待 → 取成品 |
| `eat` / `equip` / `place` | 生存与基础操作 |
| `attack` / `flee` | 近战 / 撤退 |
| `explore` | 短距离探索（虚空岛限制 24 格） |

**技能负责「脏活」**：走不到就换相邻格、挖了没捡到就换目标、缺料就补、需要工作台就放。

### 4.6 计分与策略

- `advancements.py`：分值只看难度（5/15/30/60/100），根节点/不计分项 0 分；
- `data/advancement_scores.json`：从 `Doc/RULES.md` 生成的官方表（99 项 / 3388 分）；
- `strategy.py` + `data/strategy.json`：阶段、分支优先级、目标、avoid，按文件修改时间热更新；
- 规划层会把 `strategy` 摘要、候选进度、最近失败/黑名单放进提示词。

### 4.7 预算与时间

- 每次模型调用累计 `usage`，写入状态；
- 到 98% 抛 `BudgetExceeded`，主循环体面收尾；
- 到 `COMPETITION_END` 前留 1 分钟收尾；
- 模型 402/401/403/404 **直接停**（不再空转）。

### 4.8 错误处理与防死循环（重点）

| 情况 | 行为 |
|---|---|
| `timeout` / `network_error` / `busy` / `internal` 等临时错误 | **不拉黑**，只重试 |
| `scan` / `survey` 观察类 | **不拉黑**（空转由专门检测处理） |
| 业务失败（缺料/无路/没目标…） | 连续 2 次拉黑 120 秒 |
| 决策命中黑名单 | **当轮**改做能推进的动作（砍树/采集/勘察） |
| 同一动作连续 3 次 | 强制换动作 |
| 连续 4 轮位置/背包/进度都没变 | 判定空转，强制推进 |
| 模型 402/401/403/404 | 直接停止并打印原因 |
| 断线/踢出/token 失效 | 自动重连/重登 |
| 被玩家/怪攻击（`hurt`） | 3 秒内后撤；`flee` 支持把玩家算进威胁；环境伤害（岩浆/摔落）不跑 |
| 连续死亡 | 主动休息，避开死亡循环保护 |

### 4.9 日志与可观测

- `logs/run-*.jsonl`：`decision / skill / advancement / skip_blocked / anti_repeat / stagnation / llm_error / fatal / finish`；
- `报告`：`python main.py report` 输出决策数、技能成功率、进度、token、死亡；
- **网络延迟**：**每次交互**都会记录/打印——
  - 每次 API 请求：控制台 `↔ GET /status 123ms`，并写轨迹 `latency(target=api)`；
  - 每次 LLM 调用：控制台 `🤖 LLM 812ms`，并写轨迹 `latency(target=llm)`；
  - 每次 `/status`：写轨迹 `latency(target=status)`；
  - 每个技能结束：`✅ 砍树…（4s，API 3 次 4.2s）`；
  - `report` 与 `任务记录.md` 汇总 avg/p95；
  - 嫌吵在 `config.py` 设 `PRINT_LATENCY = False`，或环境变量 `PRINT_LATENCY=false`。

> 注意：`↔` 是「一次请求的总耗时」，`/dig`、`/move_to` 这类长动作会包含服务端执行时间；
> 想看纯网络质量，用 `python main.py ping` 或看 `/status` 那一行。
>
> `python main.py mock` 用的是假服务器/脚本模型，延迟样本会是 0 或没有，属正常——
> 真实延迟统计只在连比赛服或局域网桥接时产生。
- `任务记录.md`：每次运行的人类可读总结。

### 4.10 走一遍：从原木到工作台

1. `survey` 发现附近有树、没有村庄；
2. 规划层决定 `chop_tree`；
3. 技能走到树下，自下而上挖 4 节，背包 `oak_log×4`；
4. 规划层决定 `craft crafting_table`；
5. `craft` 试探 → 服务器说缺木板 → 递归 `craft oak_planks` → 再试 → 成功；
6. 刷新进度、写轨迹、控制台打印 `✅ 合成 crafting_table×1`。

<a id="sec-learn"></a>
### 4.11 学习层（`learn` 模式）

决策层之外，还有一个**学习层**，专门做赛前 pretraining：

```bat
python main.py learn --minutes 20
```

- 目标：通过实验 + 提问，把游戏规则、配方、掉落、工具、生物/接口行为学明白；
- **先问后做**：开始时先问你目标/偏好（`must_ask`），不会一上来就执行动作；
- **按事件组织**：`events` 里的受伤/死亡/聊天/进度变化会进入提示词，作为学习情境；
- **情境记忆**：每次学习形成一段 `episode`，问答→实验→结论串起来；下一轮会读上一轮情境，动作更连贯；
- **不冲分、不读 strategy**；有疑问会停下来问你（终端输入），回答写进 memory；
- **10 轮死循环检测**：连续 10 轮动作/上下文完全相同就自动停止；模型说 `done`/`complete` 也会自动收尾；
- memory 存在 `state/knowledge.json` / `knowledge.md`，决策层每次决策都会读摘要；
- 默认只开放安全技能（survey/scan/砍树/采集/合成/熔炼/吃/装备/放置/探索）。
- 想逐条手动试动作：`python main.py learn --manual`（不调用模型，`do`/`call` 直接测）。

两个模式**每次运行前切换**：`learn` 学，`run` 打。详细说明见
[`Workflow/学习层与决策层.md`](Workflow/学习层与决策层.md)。

<a id="sec-recipe"></a>
### 4.12 配方与合成（为什么老被拒）

同一个物品常有多条配方（橡木/白桦/……木板各一条）。旧版本地桥只拿「第一条」，
你有白桦却要橡木，就会报 `missing_ingredients`；而且缺料只按 1 次算，没乘合成次数。

现在本地桥会**遍历所有配方**，按当前背包 + 请求数量选真正能做的那条；
`craft_chain` 优先用服务端返回的 `missing_total`（还差多少）补料。

详见 [`Workflow/配方与合成.md`](Workflow/配方与合成.md)。

<a id="sec-chat"></a>
### 4.13 聊天模式（每条 ≤25 字）

```bat
python main.py chat
python main.py chat --minutes 10
```

- 终端输入 → Bot 用 LLM 回复，并顺手发到游戏聊天；
- 游戏内玩家说话 → 读 `/events` 的 chat 事件 → 自动回复；
- **每条回复硬截断到 25 个字符**（`CHAT_MAX_CHARS`），system prompt 很短、不带长历史，
  单条回复 `max_tokens=48`，省 token；
- 回复前有 1.2 秒冷却，避免 `/chat` 限频。

> 聊天模式只是“说话”，不会执行挖矿/战斗；要执行动作请用 `run` / `learn --manual`。
> `BACKEND=nmo` 时会发到比赛服聊天，注意场合。

---

<a id="sec-5"></a>
## 5. 赛前测试与后续测试

### 5.1 三档测试

| 档位 | 需要什么 | 验证什么 | 成本 |
|---|---|---|---|
| ① 离线 `/mock` | Python | 技能、主循环、计分、状态、回归 | 0 |
| ② 局域网 `/local_bot` | Node + Minecraft | 真实寻路/挖/放/合成/战斗 | 0 token |
| ③ 比赛服练习 | 皮肤站账号 + 配额 | 真实接口行为、最终性能 | 少量 token |

### 5.2 每次改完代码的回归

```bat
python -m unittest discover -s tests -v
python main.py mock --ticks 20
```

88 项单测应全过；mock 应能砍树/合成/采集/记录进度。

### 5.3 局域网 15 分钟（推荐日常）

```bat
:: 桥接窗口
cd Codes\local_bot
node check.js
npm start
```

```bat
:: 另一个窗口
cd Codes
venv\Scripts\activate
python main.py reset
python main.py run --minutes 15
python main.py report
```

看四件事：**进度数、决策/分钟、技能成功率、死亡次数**。

> 没 token / 没余额：加 `--no-llm`（纯本地规则），只验证桥接与技能。

### 5.4 策略 A/B

```bat
python main.py reset
python main.py run --minutes 15
python main.py report > logs\A.txt

python main.py reset
python main.py run --minutes 15 --strategy data\strategy_nether.json
python main.py report > logs\B.txt
```

### 5.5 浸泡测试（赛前必做一次）

```bat
python main.py reset
python main.py run --minutes 120
python main.py report
```

关注：内存、日志体积、token 曲线、重连次数、死亡次数、是否卡在同一动作。

### 5.6 指标判读

| 指标 | 健康 | 异常时看什么 |
|---|---|---|
| 完成进度数 | 稳定增长 | 是否一直 `scan`/`blocked` |
| 技能成功率 | `craft/chop_tree` > 50% | 日志里的 error code |
| 决策/分钟 | 2~6 | 太高说明空转；太低说明动作太慢 |
| 网络延迟 | 状态接口 <500ms，LLM <3s | `python main.py ping` / `report` 的延迟行 |
| token/决策 | 约 1~2k | 提示词是否变长 |
| 死亡次数 | 偶尔 | 夜里/矿洞/袭击/虚空 |

### 5.7 常见失败模式

| 现象 | 根因 | 处理 |
|---|---|---|
| 大量 `402` | 模型余额不足 | 充值/换代理；程序已会直接停 |
| `ECONNREFUSED` | 端口没监听 | `node check.js`；确认世界/服务器端口 |
| Bot 不动 | 单人世界失去焦点暂停 | `pauseOnLostFocus:false` 或专用服 |
| `advancements not ready` | 本地桥不统计 | 正常，LAN 只看技能 |
| 同一动作反复 | 旧版桥接缺料误判 | 更新桥接、`reset`、重启 |

---

<a id="sec-6"></a>
## 6. 命令速查（初次 / 后续）

### 6.1 初次测试命令（按顺序）

```bat
:: 1) 环境
cd /d C:\你的路径\AIAgent4MC\Codes
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

:: 2) 配置
copy .env.example .env
notepad .env

:: 3) 离线自检（不联网）
python main.py mock --ticks 12

:: 4) 测模型
python main.py test-api

:: 5) 测比赛服 Bot（或改用局域网，见第 10 章）
python main.py test-mc

:: 5.5) 学习层（可选，赛前 pretraining；会停下来问你）
python main.py learn --ticks 10

:: 6) 第一次短跑
python main.py reset
python main.py run --minutes 10

:: 7) 看结果
python main.py report
```

### 6.2 后续测试命令

| 目的 | 命令 |
|---|---|
| 单元回归 | `python -m unittest discover -s tests -v` |
| 离线仿真 | `python main.py mock --ticks 20` |
| 学习规则/写 memory | `python main.py learn --minutes 20` |
| 手动测试动作 | `python main.py learn --manual` |
| 和 Bot 聊天 | `python main.py chat --minutes 10` |
| 清学习记忆 | `python main.py learn --reset-knowledge` |
| 看当前策略 | `python main.py strategy` |
| 局域网短跑 | `python main.py run --minutes 15` |
| 无模型模式（省钱） | `python main.py run --minutes 15 --no-llm` |
| 自由策略（md） | `python main.py run --minutes 15 --strategy data\strategy.md` |
| 换策略 A/B | `python main.py run --minutes 15 --strategy data\strategy_nether.json` |
| 看运行统计 | `python main.py report` |
| 测网络延迟 | `python main.py ping --samples 5` |
| 浸泡测试 | `python main.py run --minutes 120` |
| 正式跑（守护） | `python supervisor.py --until 18:00` |
| 运行中暂停 | `run` 直接按 `q`；交互模式输入时用 `/q`（暂停/继续，会 `/stop`） |
| 运行中下线 | `run` 直接按 `e`；交互模式输入时用 `/e`（stop → 断开 → 登出） |
| 清本地状态 | `python main.py reset` |

> 所有 `run` 都支持 `--until HH:MM`、`--minutes N`、`--ticks N`、`--reset`、`--strategy PATH`、`--no-llm`。

---

<a id="sec-7"></a>
## 7. 策略撰写与规划

### 7.1 原则：你和 LLM 接管，不预设

- **不预设具体阶段/路线/目标**：默认 `strategy.json` 是空的（mode=custom、无 phases/targets/avoid）；
- 你写一份 `data/strategy.md`（自由文本），LLM 自己阅读、拆解成“下一步做什么”；
- 策略是**软约束**，不是硬编码剧本；要严格按剧本走需要任务图调度器（尚未实现）；
- 你不写策略时，Agent 只保证生存和基础盘，不擅自冲分。

### 7.2 推荐：写 `strategy.md`

```bat
copy data\strategy.example.md data\strategy.md
notepad data\strategy.md
```

内容随便写，例如：

```markdown
目标：优先下界线，能做多深做多深；末地随缘。
时间：前 1 小时基础，之后主攻下界，最后 40 分钟扫便宜分。
约束：不要打袭击；不要碰需要酿造全药水的分。
备注：岛上有村庄就先交易。
```

启动：

```bat
python main.py run --strategy data\strategy.md
```

或直接把文件命名为 `Codes/data/strategy.md`，`run` 会自动优先读它。
用 `python main.py strategy` 可以查看当前策略（md 优先）。

### 7.3 不写策略会怎样

- `strategy.json` 为空 → 提示词告诉 LLM“用户还没写策略”；
- Agent 只做生存/基础盘（砍树、工具、食物、简单探索），不主动冲高分；
- 这符合“先学规则、别执着于现在执行比赛策略”的目标。

### 7.4 JSON 还保留吗

保留，作为兼容/备用：

```bat
python main.py run --strategy data\strategy_safe.json
```

| 字段 | 含义 |
|---|---|
| `mode` / `risk` | 打法名 / 冒险程度 |
| `phases[]` | 时间阶段（`until_min` 从本地运行算） |
| `targets` | 明确要冲的 advancement |
| `avoid` | 明确不碰 |

> 同时存在 `strategy.md` 和 `strategy.json` 时，**md 优先**，LLM 以自由文本为准；
> JSON 的 `avoid` 仍会用于候选过滤（兼容）。

### 7.5 热更新

- 改 `strategy.md` / `strategy.json`，保存后**下一个决策生效**，不用重启；
- 文件写坏不会崩：读取失败沿用上一份；
- 比赛当天可以准备 2~3 份，现场切换 `--strategy`。

### 7.6 策略要验证，不是写完就算

用 A/B（§5.4）跑同样 15 分钟，对比：

| 指标 | A | B |
|---|---|---|
| 完成进度数 | | |
| 技能成功率 | | |
| 决策/分钟 | | |
| 死亡次数 | | |
| token/决策 | | |

### 7.7 学习层与策略的关系

- 学习层（§4.11）把“游戏规则/技能经验”写进 memory；
- 决策层读 memory + strategy.md，才知道“这个策略在当前版本/当前岛况下怎么做”；
- 先 `learn` 再写策略，比直接让 LLM 猜规则靠谱得多。

> 当前技能只覆盖基础盘；下界/末地专精技能还在路线图上（附录 E）。

---

<a id="sec-8"></a>
## 8. 赛场规则（RULES.md v2）

> 官方原文：[`Doc/RULES.md`](Doc/RULES.md)。早期公告存档：[`Doc/参赛要求.md`](Doc/参赛要求.md)。

### 8.1 时间与场地

- 初赛：**11 月 22 日 10:00–18:00**，8 小时；决赛 12 月 6 日（如入围）；
- 场地：每人一座**完全相同的虚空岛**（主世界 + 下界各一座，可互相传送），四周是虚空；
- 末地**共用**；赛前末影龙已被击杀、龙蛋已移走；**禁止复活末影龙**、禁止妨碍他人；
- 只能在自己的岛和末地活动；离开自己的岛属违规；
- 比赛开始前统一重置出生点、背包、末影箱、经验、效果、进度。

### 8.2 计分

```
初赛总成绩 = 游戏表现分（60） + 工程评审分（40）
```

- 只统计官方计分表里「计分」的原版非配方 advancement；
- **每项分数只由难度决定**：5 / 15 / 30 / 60 / 100（v2 已取消深度加成）；
- 根节点 0 分；同一个 advancement 只计一次；
- 游戏表现分 = 本队原始分 ÷ 本届最高原始分 × 60；
- 并列：先比表现分 → 难度 5 数量 → 难度 4 → … → 更早达到最终分。

官方计分表：99 项计分、合计 3388 分（生成在 `Codes/data/advancement_scores.json`）。

### 8.3 资源配额

| 资源 | 初赛每队 |
|---|---|
| 运行窗口 | 8 小时 |
| 输入 token | 800 万 |
| 输出 token | 150 万 |
| 单队并发 | 4 |
| 模型/接口 | **AIA 统一代理**（禁止外部模型服务绕额度） |

### 8.4 违规（可扣分/取消成绩）

- 离开自己的岛去别人岛；
- 复活末影龙、在末地妨碍他人；
- 人工代替 Agent 操作、服务器命令/管理员权限、未公开接口、读存档；
- 利用漏洞获得 advancement。

### 8.5 规则 → 代码映射

| 规则 | 代码里的对应 |
|---|---|
| 计分只看难度、99 项表 | `data/advancement_scores.json` + `advancements.py` |
| 不冲不计分项 | 计分表 `scored=false` → 0 分，不进候选 |
| 虚空岛、别走远 | `explore` 限 24 格；提示词写明 |
| 岛上有什么决定路线 | `survey` → `island.features` → 策略 |
| 末影龙已死 | `end/kill_dragon` 等计 0，不进候选 |
| 8 小时、到点收尾 | `COMPETITION_END` + Agent 收尾 |
| token 配额 | `llm.py` 计量 + 98% 熔断 |
| 统一代理 | `.env` 的 `LLM_BASE_URL`（比赛改三行） |
| 禁止外部模型 | 代码不内置任何外部服务；只读配置 |

---

<a id="sec-9"></a>
## 9. 比赛日操作手册

### 9.1 T-7 ～ T-1

- [ ] 拿到 AIA 代理三件套，填 `.env`；
- [ ] 轮换泄露过的 key / 改皮肤站密码；
- [ ] 核对计分表（`Doc/RULES.md` 更新后重跑 `python build_scores_table.py`）；
- [ ] 真服联调 30 分钟；LAN 浸泡 2 小时；
- [ ] 准备 2~3 套 `strategy*.json`；
- [ ] 关闭睡眠/自动更新；有线网络优先。

### 9.2 当天时间线

| 时间 | 动作 |
|---|---|
| 09:30 | 开桥接/确认网络；`python main.py test-api`、`test-mc` |
| 09:50 | `python main.py reset`；确认 `strategy.json` 是今天这套 |
| 10:00 | 开跑：`python supervisor.py --until 18:00`（或 `main.py run`） |
| 10:05 | 看前几轮是否正常（登录、ready、第一轮决策） |
| 每 30~60 分钟 | 看进度/技能成功率/死亡/token |
| 17:50 | 程序自动收尾；备份 `logs/` |
| 18:00 | 记录最终进度、得分、token、死亡 |

### 9.3 什么时候干预

| 情况 | 处理 |
|---|---|
| 崩溃 | supervisor 自动拉起；不行手动 `run`（**不要 reset**） |
| 需要立刻停手 | 按 `q` 暂停；再按 `q` 继续 |
| 需要强制下线 | 按 `e`：stop → disconnect → logout；进程退出 |
| 掉线 | 自动重连；连续失败再重启 |
| 反复失败 | 重启（黑名单会保留）；或改 `strategy.json` 换阶段 |
| 402 | 程序会停；换代理/充值后重跑 |
| 快 18:00 | 不用管，自动收尾 |

### 9.4 收尾材料

- `任务记录.md`（总结）；
- `Codes/logs/run-*.jsonl`（全过程）；
- 控制台最终截图；
- 策略文件副本；
- 复盘：分数曲线、技能成功率、失败原因。

---

<a id="sec-10"></a>
## 10. 局域网与自有服务器

### 10.1 为什么不能直接连局域网

**这个项目里没有 Bot 本体。** 比赛时 Bot 跑在 NMO 的服务器上，我们只是 HTTP 客户端。
在局域网测试，需要本地跑一个 Mineflayer bot，并套一层同款 HTTP 接口 —— 这就是 `Codes/local_bot/`。

```
Minecraft 局域网世界 ← Mineflayer（local_bot/server.js）
        ↑ HTTP :8099
Codes/main.py（BACKEND=my_server）
```

### 10.2 三步

```bat
:: 1) 桥接
cd Codes\local_bot
copy config.example.json config.json
node check.js
npm start
```

```ini
:: 2) Codes/.env
BACKEND=my_server
MY_SERVER_BASE=http://127.0.0.1:8099
MY_SERVER_TOKEN=dev
```

```bat
:: 3) 验证
cd Codes
venv\Scripts\activate
python main.py status
python main.py run --minutes 10
```

### 10.3 限制

> ⚠️ **本地桥接不是比赛方服务端。** 它是本项目自己写的简化 Mineflayer 包装，
> 比赛方对接口/公式的修复只在他们服务器上；`npm install` 只会更新本地依赖，
> 不会、也不可能更新比赛方。要验证对方的修复，只能 `BACKEND=nmo` 连比赛服。
>
> 本地看起来“降智”的常见原因：
> 1. 本地桥没有 advancement 进度（`ready:false`），Agent 没有分数反馈，规划退化成基础任务；
> 2. 本地桥是简化实现（寻路参数、动作细节、窗口交互都不如比赛方完整）；
> 3. Agent 的策略/技能是本项目自己的，和比赛方服务端的内部策略无关。
>
> 本地适合验证「桥接、技能、防循环、日志」，不适合比较“智能程度”。

- 本地桥**不统计 advancement**（`ready:false`），Agent 不按进度计分；
- 附魔台/酿造台/村民交易只做了基础窗口操作；
- 载具/鞘翅不支持；
- 比赛服是主办方封装的 Mineflayer，行为可能略有差异。

### 10.4 自有服务器（以后接）

- `Codes/my_server/adapter.py` 与 `McApi` 同方法名；
- `.env` 里 `BACKEND=my_server` + `MY_SERVER_*`；
- 接口契约见 [`Codes/my_server/README.md`](Codes/my_server/README.md)。

---

<a id="sec-11"></a>
## 11. 故障排查 FAQ

| 症状 | 最可能原因 | 处理 |
|---|---|---|
| `HTTP 402` | 模型余额不足 | 充值/换代理；程序已会直接停 |
| `HTTP 401/404` | key 错 / 地址少 `/v1` | 改 `.env` |
| `ECONNREFUSED` | 端口没监听 | `node check.js`；核对游戏/服务器端口 |
| 一直 `ready=false` | 大厅/切服/未登录 | 等 20~30 秒；`test-mc` |
| Bot 不动 | 单人世界失去焦点暂停 | `pauseOnLostFocus:false` 或专用服 |
| 中文乱码 | 控制台编码 | `chcp 65001`（程序已尽量强制 UTF-8） |
| `advancements not ready` | 本地桥不统计 | 正常，LAN 只看技能 |
| 动作被拉黑/循环 | 旧桥接缺料误判 / 临时错误 | 更新桥接、`reset`、重启；新版已做防循环 |
| 配方老被拒 `missing_ingredients` | 旧桥只拿第一条配方/没乘次数 | 更新 `local_bot/server.js` 并重启桥接；见 [`配方与合成`](Workflow/配方与合成.md) |
| `need_crafting_table` | 3x3 配方没有工作台 | 先 `craft crafting_table` 再 `place` |
| `learn` 停住不动 | 学习层在等你输入 | 正常；回答或输入 `exit`/Ctrl+C |
| `strategy.md` 没生效 | 文件名/路径不对 | 放到 `Codes/data/strategy.md`，或 `--strategy data\strategy.md`；`python main.py strategy` 检查 |
| `python` 不是命令 | 没装/没加 PATH | 重装并勾选 Add to PATH |
| 单测失败 | `.env` 覆盖了 `BACKEND` | 单测已显式 patch；如仍失败看报错 |
| 策略没生效 | 文件路径/格式错 | `python main.py strategy` 检查 |
| token 花得快 | 决策太密 | 调大 `DECISION_MIN_INTERVAL`、少 `scan` |
| 延迟高/经常超时 | 网络或代理慢 | `python main.py ping` 看 DNS/TCP/TLS；TLS >800ms 考虑换网络/代理 |

---

<a id="sec-12"></a>
## 12. 文档索引

| 文档 | 内容 | 什么时候读 | 状态 |
|---|---|---|---|
| **`README.md`（本文）** | 总手册：介绍/架构/依赖/测试/命令/策略/规则/比赛日 | 任何时候先读 | ✅ 权威 |
| [`Doc/RULES.md`](Doc/RULES.md) | 官方初赛规则（草案 v2）+ 计分表 | 规则/算分有疑问 | ✅ 权威 |
| [`Doc/索引.md`](Doc/索引.md) | 完整文档索引与阅读路径 | 找文档 | ✅ 导航 |
| [`Doc/敏感信息清单.md`](Doc/敏感信息清单.md) | **开原前要删/替换的隐私清单 + 扫描脚本** | 发布前 | ✅ 维护 |
| [`Doc/参赛要求.md`](Doc/参赛要求.md) | 早期公告存档 + v2 变更说明 | 看历史 | 📚 存档 |
| [`Doc/agent-api-spec-dist.md`](Doc/agent-api-spec-dist.md) | NMO 接口规范 v1.3 | 改接口/加技能 | ✅ 权威 |
| [`Workflow/使用步骤.md`](Workflow/使用步骤.md) | 一页版操作清单 | 赛前照着做 | ✅ 补充 |
| [`Workflow/详细教程.md`](Workflow/详细教程.md) | 原理 + 逐文件说明 | 想深入 | ✅ 补充 |
| [`Workflow/harness架构.md`](Workflow/harness架构.md) | 架构设计文档（原则+分层） | 答辩/评审 | ✅ 补充 |
| [`Workflow/学习层与决策层.md`](Workflow/学习层与决策层.md) | **learn/run 双模式、memory、问答、strategy.md** | 赛前 pretraining | ✅ 专题 |
| [`Workflow/配方与合成.md`](Workflow/配方与合成.md) | 配方书来源、为什么被拒、本地修复 | 合成失败 | ✅ 专题 |
| [`Workflow/策略测试与赛前调整.md`](Workflow/策略测试与赛前调整.md) | 策略怎么写、怎么测、怎么 A/B | 调策略 | ✅ 专题 |
| [`Workflow/Harness策略框架-2026-11-13.md`](Workflow/Harness策略框架-2026-11-13.md) | 分数结构、三条路线、调度架构规划 | 定路线 | ✅ 专题 |
| [`Workflow/比赛日操作手册.md`](Workflow/比赛日操作手册.md) | 比赛当天时间线+应急 | 11/22 | ✅ 专题 |
| [`Workflow/局域网测试指南.md`](Workflow/局域网测试指南.md) | LAN 桥接完整步骤 | 局域网测试 | ✅ 专题 |
| [`Workflow/本次改动-2026-11-16.md`](Workflow/本次改动-2026-11-16.md) | v3：学习层/自由策略/接口 stop_on/配方修复 | 了解变更 | 📚 记录 |
| [`Workflow/本次改动-2026-11-13.md`](Workflow/本次改动-2026-11-13.md) | v2 改了什么、怎么迁移 | 了解变更 | 📚 记录 |
| [`Workflow/项目问题分析-2026-11-13.md`](Workflow/项目问题分析-2026-11-13.md) | 重构前的问题诊断 | 复盘/评审 | 📚 记录 |
| [`Workflow/项目问题清单.md`](Workflow/项目问题清单.md) | 已解决/剩余问题 | 查缺口 | ✅ 维护 |
| [`Workflow/记忆.md`](Workflow/记忆.md) | 给后续 AI/维护者的项目备忘 | 接手项目 | ✅ 维护 |
| [`Codes/README.md`](Codes/README.md) | 代码目录速查 | 改代码 | ✅ 补充 |
| [`Codes/my_server/README.md`](Codes/my_server/README.md) | 自有服务器适配说明 | 接自己的服 | ✅ 专题 |
| [`Codes/local_bot/README.md`](Codes/local_bot/README.md) | 局域网桥接说明 | LAN 测试 | ✅ 专题 |

> 权威性：规则以 `Doc/RULES.md` 为准，接口以 `Doc/agent-api-spec-dist.md` 为准，工程实现以代码与本文为准。

---

<a id="app-a"></a>
## 附录 A：命令与配置速查

### A.1 命令

| 命令 | 作用 |
|---|---|
| `python main.py run [--until 18:00] [--minutes N] [--reset] [--strategy PATH] [--no-llm]` | 自主运行（决策层） |
| `python main.py learn [--minutes N] [--ticks N] [--reset-knowledge]` | 学习层（学规则/问答/写 memory） |
| `python main.py learn --manual` | 手动动作测试（`do`/`call`，不花 token） |
| `python main.py chat [--minutes N]` | 和 Bot 聊天（LLM 回复，≤25 字/条） |
| `python main.py test-api` | 测模型 |
| `python main.py test-mc` | 测比赛服 Bot |
| `python main.py status` | 看 Bot 状态 |
| `python main.py strategy [--file PATH]` | 看/校验策略 |
| `python main.py report [--file PATH]` | 看运行统计（含网络延迟） |
| `python main.py ping [--samples N] [--url URL]` | 测 DNS/TCP/TLS 延迟（不花 token） |
| `python main.py mock [--ticks N]` | 离线自检 |
| `python main.py reset` | 清本地状态 |
| `python supervisor.py [--until 18:00] [--minutes N]` | 守护 + 自动重启 |
| `python -m unittest discover -s tests -v` | 单测 |
| `python build_scores_table.py` | 从 `Doc/RULES.md` 重生计分表 |
| `python scan_sensitive.py` | 开原前隐私自查（`--all` 含本机文件，`--fail` 可接 CI） |
| `node check.js` / `npm start`（local_bot） | 局域网桥接 |

### A.2 目录约定

| 路径 | 内容 |
|---|---|
| `Codes/state/world_state.json` | 状态/记忆（可删，删了=新开局） |
| `Codes/state/knowledge.json` / `.md` | 学习记忆（规则/技能/问答/实验） |
| `Codes/logs/run-*.jsonl` | 轨迹 |
| `任务记录.md`（根） | 每次运行总结 |
| `Codes/data/strategy.md` | 当前自由策略（优先） |
| `Codes/data/strategy.json` | 兼容策略（默认空） |

---

<a id="app-b"></a>
## 附录 B：文件地图

| 文件 | 职责 | 改动频率 |
|---|---|---|
| `Codes/main.py` | CLI、登录、等 ready、装配 | 低 |
| `Codes/config.py` | 配置项 | 中 |
| `Codes/.env` | 密钥/账号 | 赛前 |
| `Codes/llm.py` | 模型客户端（计量/预算/重试） | 低 |
| `Codes/planner.py` | 决策提示词、memory/strategy 注入、兜底 | 中 |
| `Codes/learn.py` | 学习层循环（实验/提问/记录） | 中 |
| `Codes/knowledge.py` | memory 读写与 Markdown 导出 | 低 |
| `Codes/skills.py` | 12 个技能 | 高（加技能） |
| `Codes/agent.py` | 主循环、保命、防循环、收尾 | 中 |
| `Codes/world_state.py` | 状态/记忆/失败/路标 | 低 |
| `Codes/advancements.py` | 计分 | 低 |
| `Codes/strategy.py` | 策略加载（md 优先/JSON 兼容） | 低 |
| `Codes/data/*.json` / `strategy.md` | 策略/计分表 | 赛前 |
| `Codes/mc_api.py` | NMO 客户端（全端点 + stop_on） | 低 |
| `Codes/trajectory.py` / `report.py` | 日志与统计 | 低 |
| `Codes/mock_api.py` | 假服务器/假大脑 | 测试 |
| `Codes/supervisor.py` | 守护进程 | 低 |
| `Codes/tests/` | 88 项单测 | 改代码时 |
| `Codes/local_bot/` | 局域网桥接（Node） | 测试 |
| `Codes/my_server/` | 自有服务器适配 | 以后 |

---

<a id="app-c"></a>
## 附录 C：术语表

| 术语 | 解释 |
|---|---|
| Harness | 把模型与游戏接口接起来的整套框架（规划/执行/记忆/容错） |
| advancement | 游戏进度（本赛道的计分单位） |
| tick | Agent 的一轮：观察→保命→决策→执行 |
| 技能（skill） | 一段确定性代码，完成一类事（如砍一棵树） |
| 策略（strategy） | 你写的自由文本（`strategy.md`），LLM 自己拆解，软约束 |
| 学习层（learn） | 赛前学规则/验证技能/问用户，写 memory，不冲分 |
| memory | `knowledge.json/md`：规则、技能经验、问答、实验记录 |
| 黑名单 | 业务失败两次后的短期禁用（120 秒） |
| 空转 | 动作成功但没有改变位置/背包/进度 |
| 802/402 | HTTP 状态：401=鉴权失败，402=余额不足 |
| 计分表 | 每项 advancement 的难度/分值/是否计分 |
| 虚空岛 | 每人一座、四周是虚空的比赛场地 |

---

<a id="app-d"></a>
## 附录 D：版本与变更

| 时间 | 版本 | 变更 |
|---|---|---|
| 2026-11-12 | v1 | 键盘模拟 + 截图识别（已废弃） |
| 2026-11-13 上午 | 检查点 `5968a88` | 重构前快照（API 版） |
| 2026-11-13 | v2 `d22e428` | 技能层/预算/状态恢复/无人值守/测试/文档 |
| 2026-11-13 | `f0a2889` | 策略框架（分数结构/路线/调度规划） |
| 2026-11-13 | `ac4497a` | 局域网桥接 + `BACKEND` 切换 |
| 2026-11-13 | `2955c56` | 端口自检/重连/ECONNREFUSED 提示 |
| 2026-11-13 | `5f5a10e` | craft 缺料误判与拉黑死循环修复 |
| 2026-11-13 | `6046940` | `strategy.json` 热更新 + `strategy/report` 命令 |
| 2026-11-13 | `53fa059` | 402 直接停、临时错误不拉黑、无进展检测、`--no-llm` |
| 2026-11-13 | `3fdc3df` | 文档整合为根 README + 独立索引 |
| 2026-11-13 | `7f64526` | 敏感信息清单 + 扫描脚本 |
| 2026-11-15 | `99d39ca` / `646f8cf` | 网络延迟检测；每次交互打印/记录 |
| 2026-11-15 | `7cffe0b` | 被打立即后撤（玩家/怪） |
| 2026-11-16 | v3（本次） | 学习层 + memory；strategy.md 自由策略；接口 `stop_on`；配方选择修复 |

> 说明：旧 `.git` 已删除并重建，表中 2026-11-13～15 的哈希仅作历史记录，新仓库不再包含这些提交。

---

<a id="app-e"></a>
## 附录 E：已知限制与路线图

### E.1 现在做不到

1. **下界/末地/村庄/试炼的专精技能**：能勘察、能规划，但还没有「造门、打要塞、酿造、附魔、交易、末地城」等技能；
2. **任务图调度器**：策略是软约束，不能保证严格按剧本执行；
3. **学习层是文本记忆**：能做实验/记录规则，但不会自动生成完整配方数据库；
4. **学习层默认只开安全技能**：不学战斗/下界/末地，避免把测试世界搞崩；
5. **本地与比赛方行为不完全一致**：本地桥是简化实现，比赛方修复只在对方服务器上；
6. **局域网 advancement 统计**：本地桥不解析进度树；
7. **多 Agent 协作**：未实现（需要第二个账号）；
8. **官方计分表可能再变**：变了要重跑 `build_scores_table.py`。

### E.2 路线图（按 ROI）

| 优先级 | 事项 | 收益 |
|---|---|---|
| P0 | 下界线：`portal` / `fortress_run` / `brew` | 775 分潜力 |
| P0 | 学习层课程表（木材→石料→工具→食物→战斗） | 规则覆盖 |
| P1 | 末地：折跃门/末地城/鞘翅 | 210 分 |
| P1 | 村庄/试炼：交易/袭击/宝库 | 150~195 分 |
| P2 | 任务图调度器（硬流程） | 稳定性/评审加分 |
| P2 | 农牧批量、附魔、锻造 | 100~200 分 |
| P3 | 多 Agent 协作 | 评审维度 |

### E.3 维护约定

- 改代码 → 跑单测 + `mock`；
- 改规则/计分表 → 重跑 `build_scores_table.py` + 更新 `Doc/RULES.md` 链接；
- 改策略 → `python main.py strategy` + A/B；
- 改学习层 → `python -m unittest` + `python main.py learn --ticks 5`；
- 每次改动更新 `Workflow/本次改动-*.md` 与 [`Doc/索引.md`](Doc/索引.md)。

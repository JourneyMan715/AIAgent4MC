# AIAgent4MC —— 复现教程（Harness 赛道版）

> **先看总手册**：[`../README.md`](../README.md)（整合了介绍/架构/测试/命令/策略/规则，带索引）。
> 本文是「从零复现」的详细教程，补充总手册没展开的步骤与截图式说明。

> 一句话：**让大模型当「大脑」，通过 NMO 的 Bot 接口控制 Minecraft 角色，
> 在 8 小时内自主吃、跑、挖、造、探索，尽可能多地完成 advancement 拿分。**
>
> 这一版和以前最大的不同：
> - **不再一个动作问一次模型**（那是 demo），而是「模型低频做规划 + 代码高频执行技能」；
> - **不再需要人守着敲任务**，开跑后自己跑 8 小时；
> - **密钥不在代码里**，在 `Codes/.env`；
> - 模型地址可配置，**比赛换成 AIA 统一代理只改三行**。

---

## 比赛怎么算分（先看这个，草案 v2）

- 场地：**每人一座相同的虚空岛**（主世界 + 下界），四周是虚空；只能在自己的岛和共用的末地活动；
- 计分：每项 advancement **只看难度**（5/15/30/60/100），**没有深度加成**；官方计分表 99 项计分（共 3385 分），其余不计分；
- 成绩 = 游戏表现分 60（按全场最高归一化）+ 工程评审 40；
- 末影龙赛前已被击杀，**禁止复活**；很多进度取决于「岛上有没有对应结构」。

> 完整规则见 `Doc/RULES.md`；代码里的计分表由它自动生成（`Codes/data/advancement_scores.json`）。
> 怎么打、先打哪条线，见 [`Harness策略框架-2026-11-13.md`](Harness策略框架-2026-11-13.md)；
> 赛前怎么测试和修改策略，见 [`策略测试与赛前调整.md`](策略测试与赛前调整.md)。

---

## 〇、先准备三样东西

| 序号 | 要准备的 | 说明 |
|---|---|---|
| ① | 一台能上网的电脑 | Windows 即可 |
| ② | **AIA 统一代理**的地址、模型名、队伍密钥 | 比赛规则要求必须走这个；赛前可先用 DeepSeek 自测 |
| ③ | 一个「皮肤站」账号 | 去 <https://skin.nmo.net.cn/> 注册，并**选定一个角色**（NMO 接口用它登录 Bot） |

> 为什么：②是「大脑的入场券」，③是「让 Bot 替你上场的门票」，Python 是「把两者接起来的胶水」。

---

## 一、整体流程

```
装 Python → 拿代码 → 装依赖 → 填 .env → 离线自检 → 测大脑 → 测 Bot → 正式跑
```

对应命令：

```bat
cd Codes
venv\Scripts\activate

python main.py mock            :: 离线自检（不联网、不花 token）
python main.py test-api        :: 测大模型连通性
python main.py test-mc         :: 测比赛服 Bot
python main.py run             :: 正式跑（比赛模式）
python supervisor.py           :: 更稳的跑法（崩溃自动重启）
```

---

## 第 1 步：安装 Python

1. 打开 <https://www.python.org/downloads/> 下载最新版；
2. 安装时**勾选 `Add Python to PATH`**；
3. 验证：`python --version`（≥ 3.10 即可）。

---

## 第 2 步：拿到代码

把整个 `AIAgent4MC` 文件夹放到电脑上（比如桌面）。

> ⚠️ 如果是从别人那里复制的文件夹，里面可能带着 `Codes\venv`——
> 那是别人电脑的 Python 环境，一般用不了。**先删掉 `Codes\venv`**，再做第 3 步。

---

## 第 3 步：装依赖

```bat
cd /d C:\你的路径\AIAgent4MC\Codes
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

看到命令行前面出现 `(venv)` 就成功了。依赖只有一个：`requests`。
（网络慢可以加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。）

---

## 第 4 步：填配置（关键）

### 4.1 复制一份 `.env`

```bat
copy .env.example .env
```

用记事本打开 `Codes\.env`，填三样东西：

```ini
# 大模型
LLM_BASE_URL=https://api.deepseek.com/v1     # 比赛时换成 AIA 代理地址
LLM_MODEL=deepseek-chat                       # 比赛时换成代理给的模型名
LLM_API_KEY=sk-你的key

# 赛事 Bot（皮肤站账号）
MC_USERNAME=你的皮肤站用户名
MC_PASSWORD=你的皮肤站密码
```

> `.env` 已经被 `.gitignore` 忽略，不会进 git；**不要**把密钥写回 `config.py`。

### 4.2 比赛前必改：换成 AIA 统一代理

规则原文：**「统一模型；接入 AIA 提供的统一代理 API；不得改用其他大模型或通过外部服务绕开额度约束。」**

拿到代理信息后，只改 `.env` 里的 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 三行，代码一行都不用动。

### 4.3 其它可选配置

都在 `Codes/config.py` 里，改之前看一眼注释：

| 想改什么 | 改哪里 |
|---|---|
| 比赛结束时间 | `COMPETITION_END = "18:00"` |
| token 预算 | `INPUT_TOKEN_BUDGET` / `OUTPUT_TOKEN_BUDGET` |
| 饿了就吃 | `EAT_BELOW_FOOD = 14` |
| 血少就跑 | `FLEE_BELOW_HEALTH = 6` |
| 多久刷一次进度 | `ADVANCEMENT_REFRESH_S = 90` |
| 决策最小间隔 | `DECISION_MIN_INTERVAL`（0 = 不限） |

---

## 第 5 步：离线自检（不联网、不花 token）

```bat
python main.py mock --ticks 12
```

它会用「假服务器 + 假大脑」跑 12 轮，验证：砍树、合成（含递归补料）、采集、放置、进度记录、状态落盘。
看到 `自检完成 ✅` 就说明代码没问题，可以连真服了。

> 想在**自己的局域网世界**里看它真的走/挖/合成：见 [`局域网测试指南.md`](局域网测试指南.md)（需要 Node.js，走 `Codes/local_bot/`）。

---

## 第 6 步：测大脑

```bat
python main.py test-api
```

正常会打印模型回复和 token 用量：

```
✅ 模型回复：连接成功
🪙 输入 13 / 8000000 tokens，输出 4 / 1500000，调用 1 次
```

- `401` → key 错；`402` → 没余额；`404` → `LLM_BASE_URL` 少了 `/v1`。

---

## 第 7 步：测 Bot

```bat
python main.py test-mc
```

会依次打印：登录成功 → 等上线 → 当前状态 → 背包 → 已下发进度数 → 最近事件。
看到「Bot 已在比赛服」就说明「手脚」通了。

| 报错 | 原因 | 解决 |
|---|---|---|
| `invalid_credentials` | 账号密码错 | 核对 `.env` |
| `no_profile` | 账号没选角色 | 去皮肤站选一个 |
| 一直 `ready=false` | 大厅/切服中 | 多等 10~30 秒；程序会自动重试 |

---

## 第 8 步：正式跑

### 8.1 新开一局

```bat
python main.py reset
```

（比赛开始前清空本地状态；比赛中途崩溃**不要** reset，直接重启会接着跑。）

### 8.2 开跑

```bat
python main.py run                :: 跑到 config.COMPETITION_END（默认 18:00）
python main.py run --until 18:00
python main.py run --minutes 30   :: 联调：只跑 30 分钟
```

### 8.3 更稳的跑法（推荐比赛当天用）

```bat
python supervisor.py --until 18:00
```

它会在主程序崩溃/退出后自动拉起，状态不丢；到点自动退出。

---

## 第 9 步：它跑到哪去了？（输出文件）

| 东西 | 位置 | 用途 |
|---|---|---|
| 世界状态 | `Codes/state/world_state.json` | 重启恢复用；别手改 |
| 轨迹日志 | `Codes/logs/run-*.jsonl` | 每一步决策/动作/进度/错误，赛后复盘 |
| 运行总结 | `任务记录.md`（项目根目录） | 人类可读：用时、轮数、新完成进度、token、死亡次数 |
| 比赛记录 | `Codes/logs/任务记录(mock).md` | 只在使用 mock 时产生 |

控制台大致长这样：

```
[12:03:41] 🧭 决策：chop_tree({"max_logs": 8}) —— 先备木头
[12:03:52] ✅ 砍树 oak_log×6（11s）
[12:03:52] 🏆 完成进度：Stone Age（+5 分）
           累计预计得分：10 分
```

---

## 第 10 步：怎么停 / 怎么恢复

- **正常停**：`Ctrl + C`（会保存状态、拉取最终进度、写任务记录）；
- **到点**：程序自己停（提前 1 分钟收尾）；
- **恢复**：直接再 `python main.py run`，会读 `state/world_state.json` 接着跑；
- **重开**：`python main.py reset`（清空状态，比赛开始前用一次）。

---

## 附录 A：命令速查

| 命令 | 作用 | 联网 | 花 token |
|---|---|---|---|
| `python main.py run` | 自主比赛模式 | 是 | 是 |
| `python main.py run --minutes 30` | 联调 30 分钟 | 是 | 是 |
| `python main.py test-api` | 测模型 | 是 | 一点点 |
| `python main.py test-mc` | 测 Bot | 是 | 否 |
| `python main.py status` | 看 Bot 状态 | 是 | 否 |
| `python main.py mock` | 离线自检 | 否 | 否 |
| `python main.py reset` | 清空本地状态 | 否 | 否 |
| `python main.py strategy` | 看当前策略/阶段 | 否 | 否 |
| `python main.py report` | 看最近一次运行统计 | 否 | 否 |
| `python supervisor.py` | 守护+自动重启 | 是 | 是 |
| `python -m unittest discover -s tests -v` | 单元测试 | 否 | 否 |

---

## 附录 B：常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| 启动就退出 | `.env` 没填/填错 | 先 `python main.py test-api` |
| `HTTP 404` | `LLM_BASE_URL` 路径不对 | 应该是 `https://域名/v1` |
| `ready=false` 很久 | 大厅排队/切服 | 等；程序自动重试 |
| 一直 `busy` | 上一个动作没结束 | 程序会等/重试；频繁出现说明接口超时 |
| token 用得快 | 决策太密 | 调大 `DECISION_MIN_INTERVAL`，减少 `scan` |
| 进度一直是 0 | `/advancements` 还没下发 | 等第一次刷新（默认 90 秒） |
| `python` 不是内部命令 | 没装/没加 PATH | 重装并勾选 Add to PATH |
| 中文乱码 | 控制台编码 | 程序已强制 UTF-8；仍乱码就 `chcp 65001` |

---

## 附录 C：安全提示（务必看）

1. **密钥只放 `.env`**，不要写进代码、截图、群聊；
2. 旧的 key 曾经进过 git 历史，**请去 DeepSeek 后台作废重发**，皮肤站密码也改一次；
3. 分享文件夹前删掉：`Codes\.env`、`Codes\venv`、`Codes\state`、`Codes\logs`、`任务记录.md`；
4. 这是竞赛用接口，只在自己账号 / 允许的服务器上使用。

---

## 附录 D：目录结构

```
AIAgent4MC/
├─ Codes/
│  ├─ main.py            入口（run / test-* / mock / reset）
│  ├─ config.py          配置（不含密钥）
│  ├─ .env               你的密钥（gitignore）
│  ├─ llm.py             大模型客户端（计量/预算/重试）
│  ├─ planner.py         规划：要一个「下一个技能」
│  ├─ skills.py          技能：砍树/采集/合成/熔炼/吃/打/跑…
│  ├─ agent.py           主循环（无人值守）
│  ├─ world_state.py     世界状态 + 记忆（落盘）
│  ├─ advancements.py    进度计分
│  ├─ mc_api.py          比赛服接口客户端
│  ├─ my_server/         ★ 以后接自己的服务器（BACKEND=my_server 切换）
│  ├─ local_bot/         ★ 局域网测试：Mineflayer 桥接（Node.js）
│  ├─ mock_api.py        离线自检
│  ├─ supervisor.py      守护进程
│  ├─ trajectory.py      轨迹日志
│  └─ tests/             单元测试
├─ Doc/
│  ├─ agent-api-spec-dist.md   接口规范
│  └─ 参赛要求.md              官方赛制（已抓取存档）
└─ Workflow/
   ├─ README.md              ← 你正在看的复现教程
   ├─ 使用步骤.md             一页版操作清单
   ├─ 详细教程.md             原理 + 每个文件干什么
   ├─ harness架构.md          架构设计文档
   ├─ 比赛日操作手册.md        比赛当天照着做
   ├─ 本次改动-2026-11-13.md  这次重构改了什么
   ├─ 项目问题分析-2026-11-13.md  问题诊断（修复前）
   ├─ 项目问题清单.md         现在还剩哪些问题
   └─ 记忆.md                给以后的 AI 看的项目备忘
```

---

祝顺利。真出问题，先看 `Codes/logs/run-*.jsonl` 最后几十行——那里有当时到底发生了什么。

# 本地 Bot 桥接（局域网测试用）

> 把「一个真实的 Mineflayer bot」包装成和 NMO 赛事 API **同形状**的 HTTP 服务。
> 这样 `Codes/` 里的 Agent 不用改一行，就能在自己的局域网世界里跑。

```
Minecraft 局域网世界  ←  Mineflayer（本目录 server.js）
        ↑
  HTTP :8099（本目录）
        ↑
Codes/main.py（BACKEND=my_server，MY_SERVER_BASE=http://127.0.0.1:8099）
```

---

## 1. 快速开始

先确认装了 **Node.js 18+**（`node -v`）。

```bat
cd Codes\local_bot
copy config.example.json config.json
:: 编辑 config.json，把 mcPort 改成你的局域网端口
npm install

:: 先自检端口（不启动 bot，只测能不能连到 Minecraft）
node check.js

:: 通了再启动桥接
npm start
```

> `npm start` 是常驻服务，**会占住这个窗口**；要敲 Python 命令请另开一个 cmd/PowerShell。
> 也可以直接双击 `start_bridge.bat`，它会单独开一个窗口跑桥接。

`node check.js` 只做一件事：测 `config.json` 里的 `host:port` 有没有程序在监听。
连不上它会直接告诉你该查什么；连上了再 `npm start`，能省掉一大半排错时间。

看到 `bot 已上线：TestBot @ [x, y, z]` 就成功了。
浏览器打开 <http://127.0.0.1:8099/health> 应该返回 `{"ok":true,"ready":true,...}`。

然后回到 `Codes/`，在 `.env` 里：

```ini
BACKEND=my_server
MY_SERVER_BASE=http://127.0.0.1:8099
MY_SERVER_TOKEN=dev
```

```bat
python main.py status
python main.py run --minutes 10
```

测完切回比赛服：把 `BACKEND` 改回 `nmo`。

---

## 2. 配置（`config.json`）

| 字段 | 含义 | 默认 |
|---|---|---|
| `mcHost` | Minecraft 地址（同机就是 127.0.0.1） | `127.0.0.1` |
| `mcPort` | **局域网端口**（Open to LAN 时聊天栏显示的那个） | `25565` |
| `mcUsername` | Bot 名字（离线模式，随便起，≤16 字符） | `TestBot` |
| `mcVersion` | 游戏版本，留空自动探测；探测失败就填 `1.21.11` 等 | `""` |
| `apiPort` | 本服务端口 | `8099` |
| `apiToken` | 简单鉴权（和 `.env` 的 `MY_SERVER_TOKEN` 一致） | `dev` |

也可以用环境变量覆盖：`MC_HOST` / `MC_PORT` / `MC_USERNAME` / `MC_VERSION` / `API_PORT` / `API_TOKEN`。

> 局域网端口**每次重新"对局域网开放"都会变**，记得改 `config.json`。

---

## 3. 支持哪些接口

| 接口 | 状态 | 说明 |
|---|---|---|
| `/status` `/inventory` | ✅ | 位置、血量、饥饿、背包、手持、维度、附近怪 |
| `/find_blocks` `/get_block` | ✅ | 支持指定方块；不带 `types` 时返回半径 24 的摘要 |
| `/get_entities` | ✅ | 敌对/动物/玩家/掉落物分类 |
| `/events` | ✅ | chat / hurt / death / pickup / kicked / disconnected |
| `/move_to` | ✅ | pathfinder，支持坐标或实体 |
| `/dig` | ✅ | 靠近 + 挖 + 收集掉落（需 `mineflayer-collectblock`） |
| `/place` | ✅ | 自动找依附面 |
| `/craft` | ✅ | 2x2 直接做；3x3 需要附近有工作台，否则返回 `need_crafting_table` |
| `/smelt`（`/window/*`） | ⚠️ | 熔炉窗口支持 put/take；其他容器基础 put/withdraw |
| `/interact` | ✅ | 方块（门/按钮/床）与实体（交易/骑乘/喂食） |
| `/use_item` | ✅ | 食物走 `consume`，其他走 `activateItem` |
| `/attack` `/shoot` | ✅ | 近战跟随攻击 / 弓弩蓄力射击 |
| `/equip` `/posture` `/dismount` `/drop` `/chat` `/stop` | ✅ | |
| `/advancements` | ❌ | 返回 `ready:false`（本地桥不解析进度树） |
| `/auth/login` | ✅ | 直接返回固定 token（不是真的账号密码） |

---

## 4. 已知限制

- **不是比赛方服务端**：这是本项目自己写的简化桥接；主办方的接口/公式修复只在他们服务器上，
  `npm install` 不会更新比赛方。要验证对方修复，请用 `BACKEND=nmo` 连比赛服。
- **不统计 advancement**：Agent 在本地跑时不会按进度计分（`planner` 会退化成基础任务），
  但走/挖/放/合成/吃/战斗这些**技能和主循环可以完整测试**。
- 附魔台、酿造台、村民交易的窗口操作只做了基础支持，复杂操作可能失败。
- 载具（船/马/矿车）驾驶、鞘翅飞行不支持（比赛接口也不支持鞘翅）。
- 与比赛服的行为可能有差异：比赛接口是主办方封装的 mineflayer，版本/参数/寻路配置可能不同。
  本地测的是「Agent 的决策与技能链路」，不是「比赛服的精确行为」。

---

## 5. 排错

| 现象 | 原因 / 解决 |
|---|---|
| `ECONNREFUSED 127.0.0.1:xxxxx` | 端口没人监听。先 `node check.js`；确认世界还开着、端口和 `config.json` 一致、专用服已启动 |
| `ECONNREFUSED` 但端口确定没错 | 服务器可能绑在局域网 IP 上（`server-ip`）：把 `mcHost` 改成 `192.168.x.x`，或把 `server-ip` 留空 |
| `ECONNREFUSED` 且 `netstat` 里只有 `[::1]` | Java 只监听了 IPv6：`mcHost` 试着填 `::1` |
| `ECONNREFUSED` | 端口不对：LAN 端口每次重开都变；或服务器没开 |
| `unsupported protocol version` | mineflayer 不支持该客户端版本：装 Paper + ViaVersion，或把 `mcVersion` 改成 mineflayer 支持的版本 |
| `bot 被踢` | 用户名冲突 / 服务器开了在线模式（LAN 世界是离线的，专用服要 `online-mode=false`） |
| 合成一直 `missing_ingredients` | 本地桥只看「当前背包 + 附近工作台」，复杂配方可能识别不到 |
| 掉落物捡不到 | 没装 `mineflayer-collectblock`，或掉落物在岩浆/虚空 |
| `advancements not implemented` | 正常，本地桥不统计进度 |
| `ready:false` 一直不消失 | bot 没进世界（看启动日志）；LAN 主机暂停了游戏 |

---

## 6. 安全

- 服务只监听 `127.0.0.1`，外网连不到；**不要**改成 `0.0.0.0`。
- 测试用单独的世界/存档，Bot 会真的挖、放、打、丢东西。
- `config.json` 里没有真实账号密码（离线模式），不用当密钥保管。

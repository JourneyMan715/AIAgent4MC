# 自定义服务器适配器（`Codes/my_server/`）

> 一句话：**这是给「以后连我自己的 Minecraft 服务器」预留的插口**。
> 现在比赛用不到它，默认没启用（`main.py` 里那两行是注释状态）。

---

## 1. 它是怎么接进来的

整套 Agent 只认「一组方法」，不关心背后是比赛服还是你自己的服：

```
agent.py / skills.py  ──调用──►  api.status() / api.dig() / api.craft() ...
                                      ▲
                     ┌────────────────┴────────────────┐
                     │                                 │
              mc_api.McApi                    my_server.MyServerApi
            （比赛服 NMO 接口）                （你自己的服务器）
```

所以换后端只要换一个对象，技能、规划、记忆、日志全都不用改。

---

## 2. 怎么启用

1. 在 `Codes/.env` 里填：

   ```ini
   MY_SERVER_BASE=https://你的服务器地址
   MY_SERVER_TOKEN=你的token
   ```

2. 打开 `Codes/main.py`，找到 `build_backend()`，把注释打开：

   ```python
   # from my_server.adapter import MyServerApi
   # api = MyServerApi(config.MY_SERVER_BASE, config.MY_SERVER_TOKEN,
   #                   timeout=config.MY_SERVER_TIMEOUT)
   ```

   同时把上面那行 `api = McApi(...)` 注释掉。

3. 验证：

   ```bat
   python main.py status
   ```

---

## 3. 你的服务器需要提供哪些接口

适配器默认按**和比赛服一样的路径/字段**写（`BASE_PATH = "/api/v1"`）。
照着实现最省事；不一样就改 `adapter.py` 顶部的 `ROUTES`。

| 方法 | 用途 | 关键参数 | 返回（`result` 里） |
|---|---|---|---|
| `GET /status` | 状态 | - | `pos/health/food/ready/held/dimension…` |
| `GET /inventory` | 背包 | - | `{items:[{name,count,slot}], …}` |
| `POST /find_blocks` | 找方块 | `types/radius/limit` | `{blocks:[{name,pos,dist}]}` |
| `POST /get_block` | 查方块 | `pos` | `{name,pos,properties,diggable…}` |
| `POST /get_entities` | 找实体 | `radius/types` | `{entities:[{id,name,pos,dist}]}` |
| `POST /events` | 事件流 | `since/limit` | `{last_seq, events:[{seq,type,…}]}` |
| `POST /advancements` | 进度 | `include_recipes` | `{advancements:[{id,title,frame,status}]}` |
| `POST /move_to` | 走 | `x/z/range` | `{pos}` |
| `POST /dig` | 挖 | `pos/collect` | `{dug,gained}` |
| `POST /place` | 放 | `item/pos` | `{placed,pos}` |
| `POST /interact` | 交互 | `pos/entity_id/item` | `{changed,…}` |
| `POST /use_item` | 用物品 | `item` | `{used,…}` |
| `POST /attack` | 攻击 | `entity_id` | `{killed,hits}` |
| `POST /craft` | 合成 | `item/count` | `{crafted,gained,inventory}` |
| `POST /window/view`、`/window/act` | 界面 | `target/ops` | `{sections,…}` |
| `POST /chat` | 聊天 | `message` | `{sent}` |
| `POST /stop` | 停 | - | `{stopped}` |

响应封装与比赛服一致最省事：

```json
{"ok": true,  "ms": 12, "result": { }}
{"ok": false, "ms": 12, "error": {"code": "missing_item", "message": "..."}}
```

适配器也兼容「直接把结果 JSON 返回」的朴素服务器（没有 `ok` 字段时原样返回）。

---

## 4. 你的服务器字段不一样，怎么改

只改 `adapter.py`，不用碰别的文件：

| 你的情况 | 改哪里 |
|---|---|
| 路径不同（`/api` 而不是 `/api/v1`） | `BASE_PATH` |
| 鉴权头不同（如 `X-Token`） | `AUTH_HEADER` / `AUTH_PREFIX` |
| 某个接口路径不同 | `ROUTES` 里对应那一行 |
| 坐标返回 `{x,y,z}` 而不是 `pos:[x,y,z]` | 在对应方法里转换一下再 `return` |
| 错误码不一样 | `_call()` 里把错误码映射成比赛服那套（技能层认 `missing_item`、`path_not_found` 等） |
| 没有某个接口（比如没有 `/advancements`） | 该方法是 `raise NotImplementedError` 或返回 `{"advancements": []}`，Agent 会降级 |

---

## 5. 自检建议

- 先 `python main.py mock`，确认 Agent 逻辑没问题（这一步和服务器无关）；
- 再 `python main.py status`，确认适配器连得上；
- 最后 `python main.py run --minutes 10` 小跑一圈，看 `Codes/logs/run-*.jsonl` 里哪一步报错。

> 注意：`skills.py` 里用到的能力是「状态 + 背包 + 找方块 + 走 + 挖 + 放 + 合成 + 攻击 + 吃东西」。
> 你的服务器至少要实现这些，Agent 才能真的玩起来。

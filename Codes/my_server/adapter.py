# -*- coding: utf-8 -*-
"""
自定义服务器适配器（给自己以后开的 Minecraft 服务器用）。

它**不是**比赛服接口的替代品，而是一个「同形状」的适配层：
把你的服务器 API 包成和 `mc_api.McApi` 一样的二十来个方法，
这样 `skills.py` / `agent.py` 一行都不用改，换后端只换一个对象。

默认按「路径和字段都跟 NMO 规范一致」来写（最省事）；
你自己的服务器不一样时，只改文件顶部的 `ROUTES` 和少数几个 `_xxx` 方法即可。

用法：
1. 在 Codes/.env 里填 MY_SERVER_BASE / MY_SERVER_TOKEN；
2. 去 main.py 的 build_backend() 里把两行注释打开（代码里已写好）；
3. `python main.py status` 验证。

支持的三种常见鉴权：
- Bearer Token（默认）：Authorization: Bearer <token>
- 自定义头：改 AUTH_HEADER
- 无鉴权：把 AUTH_HEADER 设为空字符串
"""

import time

import requests

from mc_api import McApiError


# =====================================================================
# 一、你的服务器接口长什么样？改这里就行
# =====================================================================
BASE_PATH = "/api/v1"          # 所有接口的前缀（你的服务器如果是 /api 就改这里）
AUTH_HEADER = "Authorization"   # 鉴权头名字；不需要鉴权就写成 ""
AUTH_PREFIX = "Bearer "         # token 前缀
TIMEOUT = 30                    # 单次请求超时（秒）

# 路径映射：想换路径只改右边字符串。
ROUTES = {
    "status":        "/status",
    "inventory":     "/inventory",
    "find_blocks":   "/find_blocks",
    "get_block":     "/get_block",
    "get_entities":  "/get_entities",
    "events":        "/events",
    "advancements":  "/advancements",
    "move_to":       "/move_to",
    "dig":           "/dig",
    "place":         "/place",
    "interact":      "/interact",
    "use_item":      "/use_item",
    "attack":        "/attack",
    "shoot":         "/shoot",
    "craft":         "/craft",
    "window_view":   "/window/view",
    "window_act":    "/window/act",
    "equip":         "/equip",
    "posture":       "/posture",
    "dismount":      "/dismount",
    "drop":          "/drop",
    "chat":          "/chat",
    "stop":          "/stop",
}


class MyServerApi:
    """把「你自己的服务器」包装成和 McApi 一样的接口。

    约定和比赛服一致：
    - 成功：{"ok": true, "result": {...}}（也兼容直接把 result 放在最外层）
    - 失败：{"ok": false, "error": {"code": "...", "message": "..."}}
    - 业务错误也返回 HTTP 200；HTTP 非 200 一律当错误
    """

    def __init__(self, base_url, token, *, timeout=TIMEOUT, session=None, on_request=None):
        if not base_url:
            raise ValueError("MY_SERVER_BASE 没有配置（去 Codes/.env 里填）")
        self.base_url = base_url.rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self.last_seq = 0
        self.on_request = on_request
        self._session = session or requests.Session()

    # ================= 认证 / 连接 =================
    def login(self):
        """自己的服务器通常用固定 token，不需要换票；要换就覆盖这个方法。"""
        return {"token": self.token, "bot_username": "my-server-bot"}

    def logout(self):
        return {"ok": True}

    def connect(self):
        return {"ok": True}

    def disconnect(self):
        return {"ok": True}

    # ================= 底层请求 =================
    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if AUTH_HEADER and self.token:
            headers[AUTH_HEADER] = f"{AUTH_PREFIX}{self.token}"
        return headers

    def _call(self, method, path, body=None):
        url = f"{self.base_url}{BASE_PATH}{path}"
        started = time.perf_counter()
        try:
            if method == "GET":
                resp = self._session.get(url, headers=self._headers(), timeout=self.timeout)
            else:
                resp = self._session.post(url, headers=self._headers(),
                                          json=(body if body is not None else {}),
                                          timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            self._notify(method, path, (time.perf_counter() - started) * 1000, False)
            raise McApiError("network_error", f"连不上自己的服务器：{e}")

        elapsed_ms = (time.perf_counter() - started) * 1000
        self._notify(method, path, elapsed_ms, resp.status_code == 200)

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if resp.status_code != 200:
            code, message = "http_error", f"HTTP {resp.status_code}"
            if isinstance(payload, dict):
                err = payload.get("error") or {}
                if isinstance(err, dict):
                    code = err.get("code", code)
                    message = err.get("message", message)
            raise McApiError(code, message)

        if isinstance(payload, dict) and "ok" in payload:
            if not payload.get("ok"):
                err = payload.get("error") or {}
                if not isinstance(err, dict):
                    err = {}
                raise McApiError(err.get("code", "unknown"), err.get("message", "未知错误"), err)
            return payload.get("result")

        # 兼容「直接把结果返回来」的朴素服务器
        return payload

    # ================= 观察 =================
    def status(self):
        return self._call("GET", ROUTES["status"])

    def inventory(self):
        return self._call("GET", ROUTES["inventory"])

    def find_blocks(self, types=None, radius=None, limit=None, properties=None, mature=None):
        body = {}
        for key, value in (("types", types), ("radius", radius), ("limit", limit),
                           ("properties", properties), ("mature", mature)):
            if value is not None:
                body[key] = value
        return self._call("POST", ROUTES["find_blocks"], body)

    def get_block(self, pos=None, x=None, y=None, z=None):
        return self._call("POST", ROUTES["get_block"], self._pos_body(pos, x, y, z))

    def get_entities(self, radius=None, types=None):
        body = {}
        if radius is not None:
            body["radius"] = radius
        if types is not None:
            body["types"] = types
        return self._call("POST", ROUTES["get_entities"], body)

    def events(self, since=None, limit=None):
        body = {"since": self.last_seq if since is None else since}
        if limit is not None:
            body["limit"] = limit
        result = self._call("POST", ROUTES["events"], body)
        if isinstance(result, dict) and "last_seq" in result:
            self.last_seq = result["last_seq"]
        return result

    def advancements(self, status=None, include_recipes=None):
        body = {}
        if status is not None:
            body["status"] = status
        if include_recipes is not None:
            body["include_recipes"] = include_recipes
        return self._call("POST", ROUTES["advancements"], body)

    # ================= 动作 =================
    def move_to(self, pos=None, x=None, y=None, z=None, entity_id=None, range=None,
                stop_on=None):
        body = {}
        if entity_id is not None:
            body["entity_id"] = entity_id
        elif pos is not None:
            body["pos"] = pos
        else:
            for key, value in (("x", x), ("y", y), ("z", z)):
                if value is not None:
                    body[key] = value
        if range is not None:
            body["range"] = range
        return self._call("POST", ROUTES["move_to"], self._with_stop_on(body, stop_on))

    def dig(self, pos=None, x=None, y=None, z=None, collect=None, stop_on=None):
        body = self._pos_body(pos, x, y, z)
        if collect is not None:
            body["collect"] = collect
        return self._call("POST", ROUTES["dig"], self._with_stop_on(body, stop_on))

    def place(self, item=None, pos=None, x=None, y=None, z=None, stop_on=None):
        body = {}
        if item is not None:
            body["item"] = item
        if pos is not None:
            body["pos"] = pos
        else:
            for key, value in (("x", x), ("y", y), ("z", z)):
                if value is not None:
                    body[key] = value
        return self._call("POST", ROUTES["place"], self._with_stop_on(body, stop_on))

    def interact(self, pos=None, x=None, y=None, z=None, entity_id=None, item=None,
                 stop_on=None):
        body = {}
        if entity_id is not None:
            body["entity_id"] = entity_id
        elif pos is not None:
            body["pos"] = pos
        else:
            for key, value in (("x", x), ("y", y), ("z", z)):
                if value is not None:
                    body[key] = value
        if item is not None:
            body["item"] = item
        return self._call("POST", ROUTES["interact"], self._with_stop_on(body, stop_on))

    def use_item(self, item=None, target=None, ms=None, timeout=None, source=None,
                 stop_on=None):
        body = {"item": item}
        for key, value in (("target", target), ("ms", ms), ("timeout", timeout), ("source", source)):
            if value is not None:
                body[key] = value
        return self._call("POST", ROUTES["use_item"], self._with_stop_on(body, stop_on))

    def attack(self, entity_id=None, timeout=None, stop_on=None):
        body = {"entity_id": entity_id}
        if timeout is not None:
            body["timeout"] = timeout
        return self._call("POST", ROUTES["attack"], self._with_stop_on(body, stop_on))

    def shoot(self, pos=None, entity_id=None, weapon=None, charge_ms=None,
              stop_on=None):
        body = {}
        if entity_id is not None:
            body["entity_id"] = entity_id
        elif pos is not None:
            body["pos"] = pos
        for key, value in (("weapon", weapon), ("charge_ms", charge_ms)):
            if value is not None:
                body[key] = value
        return self._call("POST", ROUTES["shoot"], self._with_stop_on(body, stop_on))

    def craft(self, item=None, count=None, stop_on=None):
        body = {"item": item}
        if count is not None:
            body["count"] = count
        return self._call("POST", ROUTES["craft"], self._with_stop_on(body, stop_on))

    def window_view(self, target=None, stop_on=None, **kwargs):
        target = target or self._normalize_target(kwargs)
        body = {"target": target} if target else {}
        return self._call("POST", ROUTES["window_view"], self._with_stop_on(body, stop_on))

    def window_act(self, target=None, ops=None, stop_on=None, **kwargs):
        target = target or self._normalize_target(kwargs)
        body = {}
        if target:
            body["target"] = target
        if ops is not None:
            body["ops"] = ops
        return self._call("POST", ROUTES["window_act"], self._with_stop_on(body, stop_on))

    def equip(self, item=None, slot=None, stop_on=None):
        body = {"item": item, "slot": slot}
        return self._call("POST", ROUTES["equip"], self._with_stop_on(body, stop_on))

    def posture(self, sneak=None, shield=None):
        body = {}
        if sneak is not None:
            body["sneak"] = sneak
        if shield is not None:
            body["shield"] = shield
        return self._call("POST", ROUTES["posture"], body)

    def dismount(self):
        return self._call("POST", ROUTES["dismount"])

    def drop(self, item=None, count=None, stop_on=None):
        body = {}
        if item is not None:
            body["item"] = item
        if count is not None:
            body["count"] = count
        return self._call("POST", ROUTES["drop"], self._with_stop_on(body, stop_on))

    def chat(self, message=None):
        return self._call("POST", ROUTES["chat"], {"message": message})

    def stop(self):
        return self._call("POST", ROUTES["stop"])

    # ================= 小工具 =================
    @staticmethod
    def _with_stop_on(body, stop_on):
        if stop_on is not None:
            body["stop_on"] = stop_on
        return body

    def _notify(self, method, path, ms, ok):
        if not self.on_request:
            return
        try:
            self.on_request(method, path, ms, ok)
        except Exception:
            pass

    @staticmethod
    def _pos_body(pos, x, y, z):
        if pos is not None:
            return {"pos": pos}
        body = {}
        for key, value in (("x", x), ("y", y), ("z", z)):
            if value is not None:
                body[key] = value
        return body

    @staticmethod
    def _normalize_target(kwargs):
        if not kwargs:
            return None
        for key in ("pos", "entity_id", "block"):
            if key in kwargs:
                return {key: kwargs[key]}
        return kwargs

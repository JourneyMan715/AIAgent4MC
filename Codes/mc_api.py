# -*- coding: utf-8 -*-
"""
比赛服 Bot 接口客户端 —— 负责「手脚」。

把接口文档（项目根目录 agent-api-spec-dist..md，v1.3）里的所有 HTTP 接口，
封装成一个个好用的方法。主程序只要调用这些方法就行，不用管 HTTP 细节。

统一约定（见接口文档第 0 节）：
- 成功：{"ok": true, "result": {...}}，这里直接返回 result。
- 失败：{"ok": false, "error": {"code": "...", "message": "..."}}，这里抛出 McApiError。
- 只有「登录」接口特殊：token 放在最外层，不在 result 里，所以 login() 单独处理。
"""

import time

import requests

from netcheck import LatencyStats


class McApiError(Exception):
    """接口返回的业务错误。code 是错误码，message 是中文说明，extra 是附带的上下文。"""

    def __init__(self, code, message, extra=None):
        self.code = code
        self.message = message
        self.extra = extra or {}
        super().__init__(f"{code}: {message}")


class McApi:
    def __init__(self, base_url, username, password, timeout=90, on_request=None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.token = None
        self.bot_username = None
        self.last_seq = 0            # 事件流游标（配合 /events 使用）
        self.latency = LatencyStats()
        self.on_request = on_request  # 回调：每次请求结束 (method, path, ms, ok)
        self._session = requests.Session()

    # ================= 认证 =================
    def login(self):
        """登录换取 token。token 在最外层（接口文档 1.1），所以要特殊处理。"""
        payload = self._request_raw(
            "POST", "auth/login",
            {"username": self.username, "password": self.password},
        )
        self.token = payload.get("token")
        self.bot_username = payload.get("bot_username")
        return payload

    def logout(self):
        try:
            return self._request("POST", "auth/logout")
        except Exception:
            return None

    # ================= 连接管理 =================
    def connect(self):
        return self._request("POST", "connect")

    def disconnect(self):
        return self._request("POST", "disconnect")

    # ================= 底层请求 =================
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request_raw(self, method, path, body=None):
        """发请求，返回整个 JSON（不做 result 提取）。"""
        url = f"{self.base_url}/{path}"
        started = time.perf_counter()
        try:
            if method == "GET":
                resp = self._session.get(url, headers=self._headers(), timeout=self.timeout)
            else:
                resp = self._session.post(url, headers=self._headers(),
                                          json=(body or {}), timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.latency.record_error()
            self._notify(method, path, elapsed_ms, False)
            raise McApiError("network_error", f"网络请求失败：{e}\n请确认能上网、能访问 {self.base_url}")

        elapsed_ms = (time.perf_counter() - started) * 1000
        if resp.status_code == 200:
            self.latency.record(elapsed_ms)
        else:
            self.latency.record_error()
        self._notify(method, path, elapsed_ms, resp.status_code == 200)

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        # HTTP 不是 200：尽量从响应体里取错误码（如登录的 invalid_credentials / no_profile）
        if resp.status_code != 200:
            code = "http_error"
            message = f"HTTP {resp.status_code}"
            if isinstance(payload, dict):
                err = payload.get("error") or {}
                code = err.get("code", code)
                message = err.get("message", message)
            raise McApiError(code, message)

        if not isinstance(payload, dict):
            raise McApiError("bad_response", f"接口返回的不是 JSON：{resp.text[:200]}")
        if not payload.get("ok"):
            err = payload.get("error") or {}
            raise McApiError(err.get("code", "unknown"), err.get("message", "未知错误"), err)
        return payload

    def _request(self, method, path, body=None):
        """发请求，直接返回 result（大多数接口都是这种封装）。"""
        return self._request_raw(method, path, body).get("result")

    # ================= 观察接口（只读，不占动作锁）=================
    def status(self):
        return self._request("GET", "status")

    def inventory(self):
        return self._request("GET", "inventory")

    def find_blocks(self, types=None, radius=None, limit=None, properties=None, mature=None):
        body = {}
        if types is not None:
            body["types"] = types
        if radius is not None:
            body["radius"] = radius
        if limit is not None:
            body["limit"] = limit
        if properties is not None:
            body["properties"] = properties
        if mature is not None:
            body["mature"] = mature
        return self._request("POST", "find_blocks", body)

    def get_block(self, pos=None, x=None, y=None, z=None):
        return self._request("POST", "get_block", self._pos_body(pos, x, y, z))

    def get_entities(self, radius=None, types=None):
        body = {}
        if radius is not None:
            body["radius"] = radius
        if types is not None:
            body["types"] = types
        return self._request("POST", "get_entities", body)

    def events(self, since=None, limit=None):
        body = {"since": self.last_seq if since is None else since}
        if limit is not None:
            body["limit"] = limit
        result = self._request("POST", "events", body)
        if isinstance(result, dict) and "last_seq" in result:
            self.last_seq = result["last_seq"]
        return result

    def advancements(self, status=None, include_recipes=None):
        body = {}
        if status is not None:
            body["status"] = status
        if include_recipes is not None:
            body["include_recipes"] = include_recipes
        return self._request("POST", "advancements", body)

    # ================= 动作接口 =================
    def move_to(self, pos=None, x=None, y=None, z=None, entity_id=None, range=None,
                stop_on=None):
        body = {}
        if entity_id is not None:
            body["entity_id"] = entity_id
        elif pos is not None:
            body["pos"] = pos
        else:
            if x is not None:
                body["x"] = x
            if y is not None:
                body["y"] = y
            if z is not None:
                body["z"] = z
        if range is not None:
            body["range"] = range
        return self._request("POST", "move_to", self._with_stop_on(body, stop_on))

    def dig(self, pos=None, x=None, y=None, z=None, collect=None, stop_on=None):
        body = self._pos_body(pos, x, y, z)
        if collect is not None:
            body["collect"] = collect
        return self._request("POST", "dig", self._with_stop_on(body, stop_on))

    def place(self, item=None, pos=None, x=None, y=None, z=None, stop_on=None):
        body = {}
        if item is not None:
            body["item"] = item
        if pos is not None:
            body["pos"] = pos
        else:
            if x is not None:
                body["x"] = x
            if y is not None:
                body["y"] = y
            if z is not None:
                body["z"] = z
        return self._request("POST", "place", self._with_stop_on(body, stop_on))

    def interact(self, pos=None, x=None, y=None, z=None, entity_id=None, item=None,
                 stop_on=None):
        body = {}
        if entity_id is not None:
            body["entity_id"] = entity_id
        elif pos is not None:
            body["pos"] = pos
        else:
            if x is not None:
                body["x"] = x
            if y is not None:
                body["y"] = y
            if z is not None:
                body["z"] = z
        if item is not None:
            body["item"] = item
        return self._request("POST", "interact", self._with_stop_on(body, stop_on))

    def use_item(self, item=None, target=None, ms=None, timeout=None, source=None,
                 stop_on=None):
        body = {}
        if item is not None:
            body["item"] = item
        if target is not None:
            body["target"] = target
        if ms is not None:
            body["ms"] = ms
        if timeout is not None:
            body["timeout"] = timeout
        if source is not None:
            body["source"] = source
        return self._request("POST", "use_item", self._with_stop_on(body, stop_on))

    def attack(self, entity_id=None, timeout=None, stop_on=None):
        body = {"entity_id": entity_id}
        if timeout is not None:
            body["timeout"] = timeout
        return self._request("POST", "attack", self._with_stop_on(body, stop_on))

    def shoot(self, pos=None, entity_id=None, weapon=None, charge_ms=None,
              stop_on=None):
        body = {}
        if entity_id is not None:
            body["entity_id"] = entity_id
        elif pos is not None:
            body["pos"] = pos
        if weapon is not None:
            body["weapon"] = weapon
        if charge_ms is not None:
            body["charge_ms"] = charge_ms
        return self._request("POST", "shoot", self._with_stop_on(body, stop_on))

    def craft(self, item=None, count=None, stop_on=None):
        body = {"item": item}
        if count is not None:
            body["count"] = count
        return self._request("POST", "craft", self._with_stop_on(body, stop_on))

    def window_view(self, target=None, stop_on=None, **kwargs):
        target = target or self._normalize_target(kwargs)
        body = {"target": target} if target else {}
        return self._request("POST", "window/view", self._with_stop_on(body, stop_on))

    def window_act(self, target=None, ops=None, stop_on=None, **kwargs):
        target = target or self._normalize_target(kwargs)
        body = {}
        if target:
            body["target"] = target
        if ops is not None:
            body["ops"] = ops
        return self._request("POST", "window/act", self._with_stop_on(body, stop_on))

    def equip(self, item=None, slot=None, stop_on=None):
        body = {"item": item, "slot": slot}
        return self._request("POST", "equip", self._with_stop_on(body, stop_on))

    def posture(self, sneak=None, shield=None):
        body = {}
        if sneak is not None:
            body["sneak"] = sneak
        if shield is not None:
            body["shield"] = shield
        return self._request("POST", "posture", body)

    def dismount(self):
        return self._request("POST", "dismount")

    def drop(self, item=None, count=None, stop_on=None):
        body = {}
        if item is not None:
            body["item"] = item
        if count is not None:
            body["count"] = count
        return self._request("POST", "drop", self._with_stop_on(body, stop_on))

    def chat(self, message=None):
        return self._request("POST", "chat", {"message": message})

    def stop(self):
        return self._request("POST", "stop")

    # ================= 小工具 =================
    @staticmethod
    def _with_stop_on(body, stop_on):
        """通用动作参数：默认由服务端决定（hurt/death），显式传入才覆盖。"""
        if stop_on is not None:
            body["stop_on"] = stop_on
        return body

    @staticmethod
    def _pos_body(pos, x, y, z):
        """把坐标参数统一成请求体：优先用 pos 数组，否则用 x/y/z。"""
        if pos is not None:
            return {"pos": pos}
        body = {}
        if x is not None:
            body["x"] = x
        if y is not None:
            body["y"] = y
        if z is not None:
            body["z"] = z
        return body

    @staticmethod
    def _normalize_target(kwargs):
        """window 接口支持扁平写法（直接传 pos/block/entity_id），统一塞进 target。"""
        if not kwargs:
            return None
        for key in ("pos", "entity_id", "block"):
            if key in kwargs:
                return {key: kwargs[key]}
        return kwargs

    # ================= 延迟统计 =================
    def _notify(self, method, path, ms, ok):
        if not self.on_request:
            return
        try:
            self.on_request(method, path, ms, ok)
        except Exception:
            pass

    def latency_stats(self):
        """接口请求耗时统计（包含服务端处理动作的时间，长动作会偏大）。"""
        return self.latency.summary()

    def latency_text(self):
        return self.latency.text()

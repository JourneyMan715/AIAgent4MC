# -*- coding: utf-8 -*-
"""
大模型客户端 —— 「大脑」的唯一出口。

设计目标（和旧版的区别）：
1. 只服务 OpenAI 兼容接口：换 AIA 代理 / 换模型只改配置，不动代码；
2. **计量**：每次调用都累计 input/output tokens，写到世界状态里，随时知道还剩多少额度；
3. **预算硬保护**：快到上限就抛 BudgetExceeded，让主循环体面收手，而不是被代理掐断；
4. **稳**：429/5xx/超时自动退避重试；401/402 立刻报错并给中文提示；
5. **JSON**：支持 response_format=json_object（代理不支持时自动降级），并自带容错解析。
"""

import json
import random
import time

import requests

from netcheck import LatencyStats


class LLMError(RuntimeError):
    """调用大模型失败（网络、鉴权、返回格式等）。"""


class LLMFatalError(LLMError):
    """不可自动恢复的错误（余额不足、key 无效、地址错）：继续跑只会空转，应停下。"""


class BudgetExceeded(LLMError):
    """token 预算用完了。"""


# ---------------------------------------------------------------------
# JSON 容错解析：模型有时会包 ```json、加解释、或者一次输出两段 JSON
# ---------------------------------------------------------------------
def _strip_code_fence(text):
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def extract_json(text):
    """从一段文字里抠出第一个合法的 JSON 对象/数组；失败返回 None。"""
    if not text:
        return None
    s = _strip_code_fence(text)
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        pass

    # 逐字符扫描，配平 {} / []（忽略字符串里的括号）
    for start, ch in enumerate(s):
        if ch not in "{[":
            continue
        open_ch = ch
        close_ch = "}" if ch == "{" else "]"
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(s)):
            c = s[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = s[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except (ValueError, TypeError):
                        break
        # 第一段没解出来就继续找下一个 '{'
    return None


class LLMClient:
    """OpenAI 兼容的聊天客户端（DeepSeek / AIA 代理都走这一套）。"""

    def __init__(
        self,
        base_url,
        model,
        api_key,
        *,
        temperature=0.2,
        max_tokens=500,
        timeout=60,
        json_mode=True,
        retries=4,
        input_budget=None,
        output_budget=None,
        stop_ratio=0.98,
        on_usage=None,
        on_call=None,
        session=None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.json_mode = bool(json_mode)
        self.retries = max(0, int(retries))
        self.input_budget = input_budget
        self.output_budget = output_budget
        self.stop_ratio = stop_ratio
        self.on_usage = on_usage
        self.on_call = on_call      # 回调：每次 HTTP 调用结束 (ms, ok)
        self.usage = {"input": 0, "output": 0, "calls": 0}
        self.latency = LatencyStats()
        self._session = session or requests.Session()

    # ---------------- 构造 ----------------
    @classmethod
    def from_config(cls, cfg, on_usage=None):
        return cls(
            base_url=cfg.LLM_BASE_URL,
            model=cfg.LLM_MODEL,
            api_key=cfg.LLM_API_KEY,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=cfg.LLM_MAX_TOKENS,
            timeout=cfg.LLM_TIMEOUT,
            json_mode=cfg.LLM_JSON_MODE,
            retries=cfg.LLM_RETRIES,
            input_budget=getattr(cfg, "INPUT_TOKEN_BUDGET", None),
            output_budget=getattr(cfg, "OUTPUT_TOKEN_BUDGET", None),
            stop_ratio=getattr(cfg, "BUDGET_STOP_RATIO", 0.98),
            on_usage=on_usage,
        )

    # ---------------- 状态 ----------------
    def budget_state(self):
        """返回 (输入已用比例, 输出已用比例)；没设预算就是 0。"""
        rin = self.usage["input"] / self.input_budget if self.input_budget else 0.0
        rout = self.usage["output"] / self.output_budget if self.output_budget else 0.0
        return rin, rout

    def remaining_input(self):
        if not self.input_budget:
            return None
        return max(0, self.input_budget - self.usage["input"])

    def _check_budget(self):
        rin, rout = self.budget_state()
        if rin >= self.stop_ratio or rout >= self.stop_ratio:
            raise BudgetExceeded(
                f"token 预算接近上限（输入 {rin:.1%} / 输出 {rout:.1%}），主动收手"
            )

    def _record_usage(self, data):
        usage = (data or {}).get("usage") or {}
        # 不同代理字段名可能不同，都兼容一下
        pin = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        pout = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        self.usage["input"] += int(pin)
        self.usage["output"] += int(pout)
        self.usage["calls"] += 1
        if self.on_usage:
            try:
                self.on_usage(self.usage)
            except Exception:
                pass

    # ---------------- 底层请求 ----------------
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages, max_tokens, temperature, json_mode):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _sleep_backoff(self, attempt):
        delay = min(20.0, (2 ** attempt) + random.uniform(0, 0.5))
        time.sleep(delay)

    # ---------------- 对外 ----------------
    def chat(self, messages, *, max_tokens=None, temperature=None, json_mode=None):
        """发一次对话，返回文本内容。失败抛 LLMError / BudgetExceeded。"""
        if not self.api_key:
            raise LLMFatalError("没有配置 LLM_API_KEY，请在 Codes/.env 里填写")
        if not self.base_url:
            raise LLMFatalError("没有配置 LLM_BASE_URL，请在 Codes/.env 里填写")

        max_tokens = max_tokens or self.max_tokens
        temperature = self.temperature if temperature is None else temperature
        json_mode = self.json_mode if json_mode is None else json_mode
        url = f"{self.base_url}/chat/completions"

        last_error = None
        for attempt in range(self.retries + 1):
            self._check_budget()
            started = time.perf_counter()
            try:
                resp = self._session.post(
                    url,
                    headers=self._headers(),
                    json=self._payload(messages, max_tokens, temperature, json_mode),
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as e:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.latency.record_error()
                self._notify_call(elapsed_ms, False)
                last_error = LLMError(f"网络请求失败：{e}")
                if attempt < self.retries:
                    self._sleep_backoff(attempt)
                    continue
                raise last_error

            elapsed_ms = (time.perf_counter() - started) * 1000
            if resp.status_code == 200:
                self.latency.record(elapsed_ms)
            else:
                self.latency.record_error()
            self._notify_call(elapsed_ms, resp.status_code == 200)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise LLMError(f"代理返回的不是 JSON：{resp.text[:300]}")
                self._record_usage(data)
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    raise LLMError(f"返回结构不对：{str(data)[:300]}")
                self._check_budget()
                return content or ""

            body = resp.text[:300]

            # 有些代理不认 response_format，收到 400 就自动降级重试一次
            if resp.status_code == 400 and json_mode and "response_format" in body:
                json_mode = False
                self.json_mode = False
                continue

            if resp.status_code in (400, 401, 402, 403, 404):
                hint = {
                    400: "请求体有误（提示词可能超长 / 字段不支持）",
                    401: "API Key 无效或过期（检查 Codes/.env 的 LLM_API_KEY）",
                    402: "账户余额不足（去 DeepSeek 充值，或换 AIA 代理）",
                    403: "没有权限（检查代理地址是否对应你们队的密钥）",
                    404: "接口路径不对（base_url 应该形如 https://xxx/v1）",
                }.get(resp.status_code, "请求失败")
                raise LLMFatalError(f"HTTP {resp.status_code}：{hint}；返回：{body}")

            if resp.status_code in (408, 429, 500, 502, 503, 504):
                last_error = LLMError(f"HTTP {resp.status_code}（临时错误）：{body}")
                if attempt < self.retries:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            time.sleep(min(30.0, float(retry_after)))
                            continue
                        except ValueError:
                            pass
                    self._sleep_backoff(attempt)
                    continue
                raise last_error

            raise LLMError(f"HTTP {resp.status_code}：{body}")

        raise last_error or LLMError("未知错误")

    def chat_json(self, messages, *, max_tokens=None, temperature=None):
        """要一个 JSON 对象。第一次没解出来会再要一次（并提示只输出 JSON）。"""
        text = self.chat(messages, max_tokens=max_tokens, temperature=temperature, json_mode=True)
        obj = extract_json(text)
        if isinstance(obj, (dict, list)):
            return obj

        retry_messages = list(messages) + [
            {"role": "assistant", "content": text[:500]},
            {"role": "user", "content": "你刚才的输出不是合法 JSON。请只输出一个 JSON 对象，不要解释、不要代码块。"},
        ]
        text2 = self.chat(retry_messages, max_tokens=max_tokens, temperature=0.0, json_mode=False)
        obj2 = extract_json(text2)
        if isinstance(obj2, (dict, list)):
            return obj2
        raise LLMError(f"模型两次都没有返回合法 JSON：{text2[:200]}")

    def usage_text(self):
        return (
            f"输入 {self.usage['input']:,} / {self.input_budget or '-'} tokens，"
            f"输出 {self.usage['output']:,} / {self.output_budget or '-'}，"
            f"调用 {self.usage['calls']} 次"
        )

    def _notify_call(self, ms, ok):
        if not self.on_call:
            return
        try:
            self.on_call(ms, ok)
        except Exception:
            pass

    def latency_stats(self):
        """LLM 调用耗时统计（不含重试间隔）。"""
        return self.latency.summary()

    def latency_text(self):
        return self.latency.text()

# -*- coding: utf-8 -*-
"""
和本地 Bot 聊天（LLM 生成回复，硬限制 25 字/条）。

两种用法：
1. 终端里直接打字 → Bot 回复，并顺手发到游戏聊天；
2. 游戏里其他玩家说话 → 读取 `/events` 的 chat 事件 → LLM 回复 → `/chat` 发出去。

启动：
    python main.py chat
    python main.py chat --minutes 10

省 token 的设计：
- system prompt 只有一句话；
- 不带长历史（只带当前这一句）；
- max_tokens 很小；
- 回复强制截断到 CHAT_MAX_CHARS（默认 25 个字符）。
"""

import queue
import threading
import time

import config
from llm import LLMError
from loopguard import LoopGuard
from mc_api import McApiError

_HERE = __file__


class ChatBot:
    def __init__(self, api, llm, *, max_chars=None, cfg=None, input_fn=None, control=None):
        self.api = api
        self.llm = llm
        self.cfg = cfg or config
        self.control = control
        self.loop_guard = LoopGuard(getattr(config, "LOOP_LIMIT", 10))
        self.max_chars = int(max_chars or getattr(config, "CHAT_MAX_CHARS", 25))
        self.input_fn = input_fn or input
        self.stop = False
        self.bot_name = str(getattr(api, "bot_username", "") or "")
        self._queue = queue.Queue()
        self._last_reply_at = 0.0
        self._thread = None

    # ================= 主循环 =================
    def run(self, deadline=None):
        print("=" * 58)
        print("💬 聊天模式：终端输入 / 游戏内聊天都会回复")
        print(f"   每条最多 {self.max_chars} 字；exit 或 Ctrl+C 退出")
        print("=" * 58)
        self._thread = threading.Thread(target=self._input_loop, daemon=True)
        self._thread.start()
        # 先消费一次历史事件，避免一启动就回复进入聊天模式前的旧消息
        try:
            self.api.events()
        except McApiError:
            pass
        try:
            while not self.stop:
                if self.control:
                    if self.control.stop:
                        break
                    if self.control.paused:
                        time.sleep(0.2)
                        continue
                if deadline and time.time() >= deadline:
                    break
                self._poll_game_chat()
                try:
                    message = self._queue.get_nowait()
                except queue.Empty:
                    message = None
                if message:
                    if message.lower() in ("exit", "quit", "退出"):
                        break
                    if message.lower() == "/q" and self.control:
                        self.control.toggle_pause()
                        continue
                    if message.lower() == "/e" and self.control:
                        self.control.emergency()
                        break
                    self.reply("你", message)
                time.sleep(float(getattr(self.cfg, "CHAT_POLL_S", 1.5)))
        except KeyboardInterrupt:
            pass
        finally:
            self.stop = True
            print("\n👋 聊天结束")

    def _input_loop(self):
        while not self.stop:
            if self.control:
                self.control.suspend()
            try:
                text = self.input_fn("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stop = True
                return
            finally:
                if self.control:
                    self.control.resume()
            if text:
                self._queue.put(text)

    def _poll_game_chat(self):
        try:
            events = self.api.events() or {}
        except McApiError as exc:
            print(f"  ⚠️ 读聊天事件失败：{exc.code}")
            return
        for event in events.get("events") or []:
            parsed = self.parse_chat(event)
            if not parsed:
                continue
            user, message = parsed
            if self.bot_name and user == self.bot_name:
                continue
            if message.startswith("/"):
                continue
            print(f"\n💬 {user}: {message}")
            self.reply(user, message)

    # ================= 回复 =================
    @staticmethod
    def parse_chat(event):
        """兼容 {type:chat,text:"Steve: hi"} 和 {type:chat,username,message}。"""
        if not isinstance(event, dict) or event.get("type") != "chat":
            return None
        user = event.get("username") or event.get("player")
        message = event.get("message") or event.get("msg")
        if not user or message is None:
            text = str(event.get("text") or "")
            if ": " not in text:
                return None
            user, message = text.split(": ", 1)
        user = str(user).strip()
        message = str(message).strip()
        if not user or not message:
            return None
        return user, message

    def clip(self, text):
        """硬限制：去掉换行/多余空白，截断到 max_chars。"""
        text = " ".join(str(text or "").split())
        text = text.strip("\"'“”‘’")
        return text[:self.max_chars] if text else "嗯？"

    def _ask_llm(self, user, message):
        system = (
            f"你是Minecraft里的Bot，正在和玩家聊天。用中文口语短句回复，"
            f"最多{self.max_chars}个字；不要解释、不要换行、不要Markdown。"
        )
        prompt = f"玩家{user}说：{message[:120]}\n只回复一句话："
        try:
            reply = self.llm.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": prompt}],
                max_tokens=48,
                temperature=0.8,
            )
        except LLMError as exc:
            print(f"  ⚠️ 模型回复失败：{exc}")
            return "嗯？"
        return self.clip(reply)

    def reply(self, user, message):
        print(f"💬 {user}: {message}")
        if self.loop_guard.hit({"user": user, "message": message}):
            print(f"  🔁 连续 {self.loop_guard.limit} 轮同一句聊天，判定死循环，停止")
            self.stop = True
            return "（停止）"
        wait = float(getattr(self.cfg, "CHAT_COOLDOWN_S", 1.2)) - (time.time() - self._last_reply_at)
        if wait > 0:
            time.sleep(wait)
        text = self._ask_llm(user, message)
        self._last_reply_at = time.time()
        print(f"🤖 {text}（{len(text)}字）")
        try:
            self.api.chat(text)
        except McApiError as exc:
            print(f"  ⚠️ 发到游戏失败：{exc.code}")
        return text

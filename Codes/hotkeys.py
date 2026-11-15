# -*- coding: utf-8 -*-
"""
运行中的强制控制（Windows 控制台热键）：

    q  暂停 / 继续（暂停时调用 /stop，立即打断走位/挖掘）
    e  强制下线（/stop → disconnect → logout，并退出主循环）

只在 `python main.py run` 里启用；`learn` / `chat` / `learn --manual`
本身要读终端输入，热键会抢字符，所以不启用（用 Ctrl+C / exit）。
"""

import os
import sys
import threading
import time


class HotkeyController:
    def __init__(self, api):
        self.api = api
        self.paused = False
        self.stop = False
        self.reason = ""
        self._running = False
        self._thread = None

    # ================= 生命周期 =================
    def start(self):
        enabled = os.name == "nt" and sys.stdin.isatty()
        if not enabled:
            print("⌨️ 热键不可用（非 Windows 控制台）。用 Ctrl+C 中断。")
            return False
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("⌨️ 热键：q 暂停/继续，e 强制下线")
        return True

    def shutdown(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _loop(self):
        try:
            import msvcrt
        except ImportError:
            return
        while self._running and not self.stop:
            try:
                if msvcrt.kbhit():
                    key = msvcrt.getwch().lower()
                    if key == "q":
                        self.toggle_pause()
                    elif key == "e":
                        self.emergency()
            except Exception:
                pass
            time.sleep(0.05)

    # ================= 动作 =================
    def toggle_pause(self):
        if self.stop:
            return
        self.paused = not self.paused
        if self.paused:
            print("\n⏸ 已暂停（q 继续，e 强制下线）")
            self._safe("stop")
        else:
            print("\n▶ 继续运行")

    def emergency(self):
        if self.stop:
            return
        self.stop = True
        self.reason = "用户按 e 强制下线"
        print("\n🛑 强制下线：stop → disconnect → logout")
        self._safe("stop")
        self._safe("disconnect")
        self._safe("logout")

    def _safe(self, method):
        fn = getattr(self.api, method, None)
        if not callable(fn):
            return
        try:
            fn()
        except Exception as exc:
            print(f"  ⚠️ {method} 失败：{getattr(exc, 'code', type(exc).__name__)}")

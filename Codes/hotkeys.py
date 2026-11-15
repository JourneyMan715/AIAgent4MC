# -*- coding: utf-8 -*-
"""
运行中的强制控制（Windows 全局热键，所有模式可用）：

    q  暂停 / 继续（暂停时调用 /stop，立即打断走位/挖掘）
    e  强制下线（/stop → disconnect → logout；交互模式会直接退出进程）

实现：
- 优先用 Windows 低级键盘钩子（只观察、不吞键），所以 `learn` / `chat` /
  `learn --manual` 里正在 `input()` 时也能按 q/e；
- 钩子不可用时，`run` 退回 msvcrt 轮询（但交互模式会抢输入，不启用）；
- 钩子回调只改标志/入队，API 调用放到 worker 线程，避免卡住系统钩子。
"""

import ctypes
import ctypes.wintypes as wintypes
import os
import queue
import sys
import threading
import time

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
VK_Q = 0x51
VK_E = 0x45


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                              wintypes.WPARAM, wintypes.LPARAM)


class HotkeyController:
    def __init__(self, api, *, allow_msvcrt=False, force_exit=False):
        self.api = api
        self.allow_msvcrt = allow_msvcrt
        self.force_exit = force_exit
        self.paused = False
        self.stop = False
        self.reason = ""
        self._running = False
        self._queue = queue.Queue()
        self._worker = None
        self._hook_thread = None
        self._hook_ready = threading.Event()
        self._hook_ok = False
        self._hook_thread_id = 0
        self._hook_handle = None
        self._hook_proc = None
        self._hook_error = 0
        self._fallback_thread = None
        self._input_active = False

    # ================= 生命周期 =================
    def start(self):
        self._running = True
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

        if os.name != "nt" or not sys.stdin.isatty():
            print("⌨️ 热键不可用（非 Windows 控制台）；用 Ctrl+C 中断。")
            return False

        if self._start_hook():
            print("⌨️ 热键：q 暂停/继续，e 强制下线（所有模式可用）")
            return True

        if self.allow_msvcrt:
            self._start_msvcrt()
            print("⌨️ 热键：q 暂停/继续，e 强制下线（msvcrt 模式）")
            return True

        print("⌨️ 热键不可用（键盘钩子安装失败）；用 Ctrl+C / exit。")
        return False

    def shutdown(self):
        self._running = False
        try:
            self._queue.put(("stop", None))
        except Exception:
            pass
        if self._hook_handle:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._hook_thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        if self._hook_thread and self._hook_thread.is_alive():
            self._hook_thread.join(timeout=1.0)

    # ================= 钩子 =================
    def _start_hook(self):
        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()
        self._hook_ready.wait(timeout=1.0)
        return self._hook_ok

    def _hook_loop(self):
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            self._hook_thread_id = kernel32.GetCurrentThreadId()
            self._hook_proc = HOOKPROC(self._hook_callback)
            self._hook_handle = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._hook_proc,
                kernel32.GetModuleHandleW(None), 0)
            if not self._hook_handle:
                self._hook_error = ctypes.get_last_error()
                self._hook_ok = False
                self._hook_ready.set()
                return
            self._hook_ok = True
            self._hook_ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None
        except Exception as exc:
            self._hook_error = repr(exc)
            self._hook_ok = False
            self._hook_ready.set()

    def _hook_callback(self, n_code, w_param, l_param):
        try:
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == VK_Q:
                    self._on_key("q")
                elif kb.vkCode == VK_E:
                    self._on_key("e")
        except Exception:
            pass
        return ctypes.windll.user32.CallNextHookEx(None, n_code, w_param, l_param)

    # ================= msvcrt 兜底（run 用） =================
    def _start_msvcrt(self):
        self._fallback_thread = threading.Thread(target=self._msvcrt_loop, daemon=True)
        self._fallback_thread.start()

    def _msvcrt_loop(self):
        try:
            import msvcrt
        except ImportError:
            return
        while self._running and not self.stop:
            try:
                if not self._input_active and msvcrt.kbhit():
                    self._on_key(msvcrt.getwch().lower())
            except Exception:
                pass
            time.sleep(0.05)

    # ================= 按键处理 =================
    def suspend(self):
        """临时关闭 msvcrt 轮询（正在 input() 时避免抢字符）。"""
        self._input_active = True

    def resume(self):
        self._input_active = False

    def _on_key(self, key):
        # 正在 input() 时不抢键：否则用户回答里的 q/e 会误触
        if self._input_active:
            return
        if key == "q" and not self.stop:
            self.paused = not self.paused
            self._queue.put(("pause", self.paused))
        elif key == "e" and not self.stop:
            self.stop = True
            self.reason = "用户按 e 强制下线"
            self._queue.put(("emergency", None))

    def _work(self):
        while self._running:
            try:
                kind, value = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "stop":
                break
            if kind == "pause":
                if value:
                    print("\n⏸ 已暂停（q 继续，e 强制下线）")
                    self._safe("stop")
                else:
                    print("\n▶ 继续运行")
            elif kind == "emergency":
                self.emergency(force=True)

    # ================= 直接调用（测试/手动） =================
    def toggle_pause(self):
        if self.stop:
            return
        self.paused = not self.paused
        if self.paused:
            print("\n⏸ 已暂停（q 继续，e 强制下线）")
            self._safe("stop")
        else:
            print("\n▶ 继续运行")

    def emergency(self, force=False):
        if self.stop and not force:
            return
        self.stop = True
        self.reason = self.reason or "用户按 e 强制下线"
        print("\n🛑 强制下线：stop → disconnect → logout")
        self._safe("stop")
        self._safe("disconnect")
        self._safe("logout")
        if self.force_exit:
            time.sleep(0.2)
            os._exit(0)

    def _safe(self, method):
        fn = getattr(self.api, method, None)
        if not callable(fn):
            return
        try:
            fn()
        except Exception as exc:
            print(f"  ⚠️ {method} 失败：{getattr(exc, 'code', type(exc).__name__)}")

# -*- coding: utf-8 -*-
"""
守护进程（可选）—— 比赛时更稳的跑法。

作用：主程序如果崩了/被系统杀掉/网络断开退出，自动拉起来接着跑
（状态存在 `Codes/state/world_state.json`，重启不会失忆）。

用法：
    python supervisor.py                      # 跑到 config.COMPETITION_END
    python supervisor.py --until 18:00        # 指定结束时间
    python supervisor.py --minutes 30         # 只跑 30 分钟（联调）
    python supervisor.py --reset              # 新开一局（清空状态）

建议：比赛当天用这个，而不是直接 `python main.py run`。
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config


def compute_deadline(until, minutes):
    if minutes:
        return time.time() + minutes * 60
    target_text = until or config.COMPETITION_END
    try:
        hour, minute = [int(x) for x in str(target_text).split(":")[:2]]
    except (ValueError, TypeError):
        print(f"结束时间「{target_text}」看不懂，按不设截止处理")
        return None
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        print(f"结束时间 {target_text} 今天已经过了，按不设截止处理")
        return None
    return target.timestamp()


def main():
    parser = argparse.ArgumentParser(description="带自动重启的比赛守护进程")
    parser.add_argument("--until", default=None, help="结束时间 HH:MM")
    parser.add_argument("--minutes", type=int, default=0, help="只跑多少分钟")
    parser.add_argument("--ticks", type=int, default=0, help="最多决策轮数（透传给 main.py）")
    parser.add_argument("--reset", action="store_true", help="新开一局（清空本地状态）")
    parser.add_argument("--max-restarts", type=int, default=80, help="最多重启多少次")
    parser.add_argument("--restart-delay", type=int, default=5, help="崩溃后等几秒再拉起")
    args = parser.parse_args()

    deadline = compute_deadline(args.until, args.minutes)
    command = [sys.executable, os.path.join(_HERE, "main.py"), "run"]
    if args.until:
        command += ["--until", args.until]
    if args.minutes:
        command += ["--minutes", str(args.minutes)]
    if args.ticks:
        command += ["--ticks", str(args.ticks)]
    if args.reset:
        command += ["--reset"]

    print("=" * 58)
    print(" 守护模式：主程序崩溃会自动重启")
    print(f" 结束时间：{time.strftime('%H:%M', time.localtime(deadline)) if deadline else '不设限制'}")
    print("=" * 58)

    restarts = 0
    quick_failures = 0

    while True:
        if deadline and time.time() >= deadline:
            print("到点，守护进程退出")
            return 0

        started = time.time()
        code = subprocess.call(command, cwd=_HERE)
        lasted = time.time() - started

        if deadline and time.time() >= deadline:
            print("到点，守护进程退出")
            return 0

        restarts += 1
        if lasted < 10:
            quick_failures += 1
        else:
            quick_failures = 0

        if quick_failures >= 3:
            print("⚠️ 连续 3 次刚启动就退出，可能是账号/token/预算问题，停止重启。")
            print("   先用 python main.py test-mc / test-api 排查。")
            return 1
        if args.max_restarts and restarts > args.max_restarts:
            print(f"⚠️ 重启次数超过 {args.max_restarts}，停止。")
            return 1

        print(f"主程序退出（code={code}，跑了 {lasted / 60:.1f} 分钟），"
              f"{args.restart_delay} 秒后自动重启……")
        time.sleep(args.restart_delay)


if __name__ == "__main__":
    sys.exit(main())

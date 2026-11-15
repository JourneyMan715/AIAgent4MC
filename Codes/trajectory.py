# -*- coding: utf-8 -*-
"""
运行轨迹与总结（给人看 + 给评委看）。

- 轨迹：`Codes/logs/run-*.jsonl`，每行一个事件（决策、动作、进度、错误、token）。
  比赛后可以拿它复盘、画分数曲线、剪高光片段。
- 总结：`任务记录.md`，人类可读的一段。
"""

import json
import os
import time


class Trajectory:
    def __init__(self, log_dir, run_id=None, echo=False):
        os.makedirs(log_dir, exist_ok=True)
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(log_dir, f"run-{self.run_id}.jsonl")
        self.echo = echo
        self._fh = open(self.path, "a", encoding="utf-8")
        self.count = 0

    def log(self, kind, **fields):
        record = {"t": round(time.time(), 3), "kind": kind}
        record.update(fields)
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
            self.count += 1
        except OSError:
            pass

    def close(self):
        try:
            self._fh.close()
        except OSError:
            pass


def _fmt_tokens(n):
    return f"{int(n):,}" if n is not None else "-"


def write_task_log(path, *, started_at, ended_at, ticks, actions, new_advancements,
                   total_score, usage, deaths, trajectory_path, stop_reason,
                   input_budget=None, output_budget=None, net_stats=None):
    """往「任务记录.md」追加一节人类可读的总结。"""
    duration = max(0, int(ended_at - started_at))
    hours, rem = divmod(duration, 3600)
    minutes, seconds = divmod(rem, 60)

    lines = [
        "",
        "---",
        "",
        f"## 运行 {time.strftime('%Y-%m-%d %H:%M', time.localtime(started_at))}"
        f" → {time.strftime('%H:%M', time.localtime(ended_at))}",
        "",
        f"- **结束原因**：{stop_reason}",
        f"- **用时**：{hours} 小时 {minutes} 分 {seconds} 秒",
        f"- **决策轮数**：{ticks} 轮，动作 {actions} 次",
        f"- **新完成进度**：{len(new_advancements)} 项，累计预计得分 {total_score}",
        f"- **token**：输入 {_fmt_tokens(usage.get('input'))} / {_fmt_tokens(input_budget)}，"
        f"输出 {_fmt_tokens(usage.get('output'))} / {_fmt_tokens(output_budget)}，"
        f"调用 {usage.get('calls', 0)} 次",
        f"- **死亡次数**：{deaths}",
        f"- **轨迹文件**：`{os.path.basename(trajectory_path)}`",
    ]
    if new_advancements:
        lines.append("- **本次完成的进度**：")
        for item in new_advancements[:30]:
            lines.append(f"  - `{item.get('id')}` {item.get('title', '')}（{item.get('score', 0)} 分）")
        if len(new_advancements) > 30:
            lines.append(f"  - …… 其余 {len(new_advancements) - 30} 项见轨迹文件")

    if net_stats:
        def _fmt(item):
            if not item or not item.get("n"):
                return "无样本"
            return (f"avg {item['avg_ms']:.0f}ms / p95 {item['p95_ms']:.0f}ms"
                    f"（{item['n']} 次，错误 {item.get('errors', 0)}）")

        lines.append(
            f"- **网络延迟**：状态接口 {_fmt(net_stats.get('status'))}；"
            f"LLM {_fmt(net_stats.get('llm'))}；接口请求 {_fmt(net_stats.get('api'))}"
        )
    lines.append("")

    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("[记录] 写入失败：", e)

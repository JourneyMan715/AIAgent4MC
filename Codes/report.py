# -*- coding: utf-8 -*-
"""
轨迹报告 —— 把 `logs/run-*.jsonl` 压成一张表，用来对比不同策略。

用法：
    python main.py report                 # 最近一次运行
    python main.py report --file logs/run-xxx.jsonl
"""

import glob
import json
import os


def latest_log(log_dir):
    files = glob.glob(os.path.join(log_dir, "run-*.jsonl"))
    if not files:
        return None
    files.sort(key=os.path.getmtime)
    return files[-1]


def summarize(path):
    stats = {
        "path": path,
        "start": None,
        "end": None,
        "decisions": 0,
        "kinds": {},
        "skills": {},
        "advancements": [],
        "usage": None,
        "score": None,
        "stop": None,
        "deaths": 0,
        "latency": {},
    }
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            t = record.get("t")
            if isinstance(t, (int, float)):
                stats["start"] = t if stats["start"] is None else min(stats["start"], t)
                stats["end"] = t if stats["end"] is None else max(stats["end"], t)

            kind = record.get("kind")
            stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1

            if kind == "decision":
                stats["decisions"] += 1
            elif kind == "skill":
                name = record.get("skill") or "?"
                item = stats["skills"].setdefault(name, {"ok": 0, "fail": 0, "ms": 0})
                if record.get("ok"):
                    item["ok"] += 1
                else:
                    item["fail"] += 1
                item["ms"] += int(record.get("ms") or 0)
            elif kind == "advancement":
                stats["advancements"].append(record.get("id"))
            elif kind == "latency":
                target = record.get("target") or "?"
                try:
                    stats["latency"].setdefault(target, []).append(float(record.get("ms") or 0))
                except (TypeError, ValueError):
                    pass
            elif kind == "event" and record.get("type") == "death":
                stats["deaths"] += 1
            elif kind == "finish":
                stats["usage"] = record.get("usage")
                stats["score"] = record.get("score")
                stats["stop"] = record.get("reason")
    return stats


def format_report(stats):
    if not stats:
        return "（没有可读的轨迹文件）"
    lines = []
    duration = 0
    if stats["start"] and stats["end"]:
        duration = max(0, stats["end"] - stats["start"])
    lines.append(f"文件：{stats['path']}")
    lines.append(f"时长：{duration / 60:.1f} 分钟，决策 {stats['decisions']} 次")
    if stats["stop"]:
        lines.append(f"结束：{stats['stop']}")
    if stats["advancements"]:
        lines.append(f"进度：{len(stats['advancements'])} 项（{', '.join(stats['advancements'][:6])}）")
    if stats.get("score") is not None:
        lines.append(f"预计得分：{stats['score']}")

    skills = sorted(stats["skills"].items(), key=lambda kv: -(kv[1]["ok"] + kv[1]["fail"]))
    if skills:
        lines.append("技能：")
        for name, item in skills:
            total = item["ok"] + item["fail"]
            rate = item["ok"] / total * 100 if total else 0
            seconds = item["ms"] / 1000.0
            lines.append(f"  - {name:10s} {item['ok']}/{total} 成功（{rate:.0f}%），{seconds:.0f}s")
    usage = stats.get("usage") or {}
    if usage:
        lines.append(
            f"token：输入 {int(usage.get('input', 0)):,}，输出 {int(usage.get('output', 0)):,}，"
            f"调用 {int(usage.get('calls', 0)):,} 次"
        )
    if stats.get("latency"):
        parts = []
        for target, values in stats["latency"].items():
            if not values:
                continue
            ordered = sorted(values)
            avg = sum(ordered) / len(ordered)
            p95 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]
            parts.append(f"{target} avg {avg:.0f}ms / p95 {p95:.0f}ms（{len(ordered)} 次）")
        if parts:
            lines.append("网络延迟：" + "；".join(parts))
    if stats.get("deaths"):
        lines.append(f"死亡：{stats['deaths']} 次")
    return "\n".join(lines)

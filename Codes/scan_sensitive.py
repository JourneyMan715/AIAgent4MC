# -*- coding: utf-8 -*-
"""
敏感信息自查（开原前跑一遍）

用法：
    python scan_sensitive.py            # 只扫 git 跟踪的文件（会发布的部分）
    python scan_sensitive.py --all      # 连 .env / 启动器 .bat / local_bot/config.json / logs / state 一起扫
    python scan_sensitive.py --fail     # 发现高危项时返回 1（可接 CI）

说明：
- 输出只显示**掩码**，不会把完整密钥打印出来；
- 127.0.0.1/localhost/公开域名/示例占位符不算敏感；
- 端口、路径、用户名属于「环境相关」，按 P1 处理，见 Doc/敏感信息清单.md。
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EXCLUDE_DIRS = {".git", "venv", "node_modules", "__pycache__", ".dsh-tmp", ".idea", ".vscode"}

# (级别, 标签, 正则)
HIGH = [
    ("P0", "LLM_API_KEY", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    # 只匹配「真正的字面量密码」：.env 的 KEY=value 或旧 config 的 KEY="value"
    ("P0", "MC_PASSWORD", re.compile(
        r"^\s*MC_PASSWORD\s*=\s*[\"']?([A-Za-z0-9_\-!@#$%^&*.]{4,})[\"']?\s*$")),
    ("P0", "ACCESS_TOKEN", re.compile(r"eyJ[A-Za-z0-9_-]{30,}")),
]

MEDIUM = [
    ("P1", "WINDOWS_USER", re.compile(r"C:\\Users\\([^\\\s\"']+)")),
    ("P1", "PERSONAL_PATH", re.compile(r"D:\\Games\\[^\"'\s]+")),
    ("P1", "EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]

LOW = [
    ("P2", "PRIVATE_IP", re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("P2", "DEV_TOKEN", re.compile(r"(?:apiToken|MY_SERVER_TOKEN)\s*[:=]\s*[\"']?dev\b")),
]


def mask(value):
    value = str(value)
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "…" + value[-4:]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def tracked_files():
    try:
        out = subprocess.check_output(
            ["git", "-C", ROOT, "ls-files", "-z"], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.decode("utf-8", "ignore").split("\x00") if p]


def walk_files():
    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in names:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            files.append(rel)
    return files


def scan(files, self_rel):
    findings = []
    seen = set()
    for rel in files:
        if rel == self_rel:
            continue
        full = os.path.join(ROOT, rel)
        text = read_text(full)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for level, label, pattern in HIGH + MEDIUM + LOW:
                match = pattern.search(line)
                if not match:
                    continue
                key = (rel, lineno, label)
                if key in seen:
                    continue
                seen.add(key)
                value = match.group(1) if match.groups() else match.group(0)
                findings.append((level, rel, lineno, label, mask(value), line.strip()[:110]))
    return findings


def main():
    parser = argparse.ArgumentParser(description="敏感信息自查（只输出掩码）")
    parser.add_argument("--all", action="store_true", help="连被忽略的本机文件一起扫")
    parser.add_argument("--fail", action="store_true", help="有 P0 时返回 1")
    args = parser.parse_args()

    self_rel = os.path.relpath(os.path.abspath(__file__), ROOT).replace("\\", "/")
    tracked = tracked_files()
    if args.all or tracked is None:
        files = walk_files()
        if tracked is None:
            print("⚠️ 当前目录不是 git 仓库，改为扫描全部文件（等价 --all）")
    else:
        files = tracked
    findings = scan(files, self_rel)

    print(f"扫描范围：{'全部文件（含本机文件）' if args.all else 'git 跟踪文件'}，共 {len(files)} 个")
    if not findings:
        print("✅ 没有发现敏感信息")
        return 0

    order = {"P0": 0, "P1": 1, "P2": 2}
    findings.sort(key=lambda x: (order.get(x[0], 9), x[1], x[2]))
    for level, rel, lineno, label, masked, snippet in findings:
        print(f"[{level}] {rel}:{lineno}  {label:12s} {masked:12s}  {snippet}")

    high = sum(1 for f in findings if f[0] == "P0")
    print(f"\n合计 {len(findings)} 处：P0={high}，P1={sum(1 for f in findings if f[0]=='P1')}，"
          f"P2={sum(1 for f in findings if f[0]=='P2')}")
    print("处理清单见 Doc/敏感信息清单.md")
    if args.fail and high:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

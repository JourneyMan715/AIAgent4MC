# -*- coding: utf-8 -*-
"""
网络延迟检测（DNS / TCP / TLS / 总耗时）。

用途：
1. `python main.py ping`：赛前/现场测大模型、比赛服 API 的网络延迟；
2. Agent 运行时记录每次 `/status` 与 LLM 调用的耗时，低开销、可复盘；
3. 不发送任何业务请求（只做 TCP/TLS 握手），不消耗 token、不需要登录。

指标说明：
- dns_ms   域名解析耗时
- tcp_ms   TCP 建连耗时
- tls_ms   TLS 握手耗时（HTTPS 才有）
- total_ms 从开始解析到连接可用
"""

import socket
import ssl
import time
from collections import deque
from urllib.parse import urlsplit


def _percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * ratio
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


class LatencyStats:
    """滚动窗口内的延迟统计（毫秒）。"""

    def __init__(self, maxlen=200):
        self.values = deque(maxlen=max(1, int(maxlen)))
        self.errors = 0
        self.last = None

    def record(self, ms):
        value = max(0.0, float(ms))
        self.values.append(value)
        self.last = value
        return value

    def record_error(self):
        self.errors += 1

    def summary(self):
        values = list(self.values)
        if not values:
            return {
                "n": 0, "errors": self.errors, "last_ms": None,
                "avg_ms": None, "p50_ms": None, "p95_ms": None,
                "min_ms": None, "max_ms": None,
            }
        return {
            "n": len(values),
            "errors": self.errors,
            "last_ms": round(values[-1], 1),
            "avg_ms": round(sum(values) / len(values), 1),
            "p50_ms": round(_percentile(values, 0.50), 1),
            "p95_ms": round(_percentile(values, 0.95), 1),
            "min_ms": round(min(values), 1),
            "max_ms": round(max(values), 1),
        }

    def text(self):
        data = self.summary()
        if not data["n"]:
            return f"无样本（错误 {data['errors']} 次）"
        return (f"avg {data['avg_ms']:.0f}ms / p95 {data['p95_ms']:.0f}ms "
                f"/ max {data['max_ms']:.0f}ms（{data['n']} 次，错误 {data['errors']}）")


def ping_host(host, port, timeout=3.0, use_tls=False):
    """测一次 host:port 的 DNS/TCP/TLS 耗时，不发送应用层数据。"""
    result = {
        "host": host, "port": int(port), "ok": False, "error": None,
        "dns_ms": None, "tcp_ms": None, "tls_ms": None, "total_ms": None,
    }
    if not host:
        result["error"] = "host 为空"
        return result

    started = time.perf_counter()
    sock = None
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        dns_end = time.perf_counter()
        result["dns_ms"] = (dns_end - started) * 1000

        family, socktype, proto, _, addr = infos[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(addr)
        tcp_end = time.perf_counter()
        result["tcp_ms"] = (tcp_end - dns_end) * 1000

        if use_tls:
            context = ssl.create_default_context()
            tls_sock = context.wrap_socket(sock, server_hostname=host)
            tls_end = time.perf_counter()
            result["tls_ms"] = (tls_end - tcp_end) * 1000
            result["total_ms"] = (tls_end - started) * 1000
            tls_sock.close()
            sock = None
        else:
            result["total_ms"] = (tcp_end - started) * 1000

        result["ok"] = True
    except Exception as exc:  # DNS/连接/证书错误都归到这里
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["total_ms"] = (time.perf_counter() - started) * 1000
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return result


def ping_url(url, samples=5, timeout=3.0):
    """对 URL 的 host:port 采样多次。"""
    raw = url if "://" in str(url) else f"https://{url}"
    parts = urlsplit(raw)
    use_tls = parts.scheme == "https"
    host = parts.hostname
    port = parts.port or (443 if use_tls else 80)
    results = [ping_host(host, port, timeout=timeout, use_tls=use_tls)
               for _ in range(max(1, int(samples)))]
    return {
        "url": raw,
        "host": host,
        "port": port,
        "tls": use_tls,
        "samples": results,
    }


def summarize_ping(result):
    """把多次采样压成 avg/p95。"""
    rows = result.get("samples") or []
    ok_rows = [r for r in rows if r.get("ok")]
    summary = {
        "url": result.get("url"),
        "host": result.get("host"),
        "port": result.get("port"),
        "ok_count": len(ok_rows),
        "fail_count": len(rows) - len(ok_rows),
    }
    for field in ("dns_ms", "tcp_ms", "tls_ms", "total_ms"):
        values = [r[field] for r in ok_rows if r.get(field) is not None]
        summary[field] = {
            "avg": round(sum(values) / len(values), 1) if values else None,
            "p95": round(_percentile(values, 0.95), 1) if values else None,
            "max": round(max(values), 1) if values else None,
        }
    summary["errors"] = [r.get("error") for r in rows if r.get("error")]
    return summary


def format_ping(name, result):
    """生成人类可读的多行结果。"""
    summary = summarize_ping(result)
    host = summary.get("host") or "?"
    port = summary.get("port") or "?"
    lines = [f"{name}：{host}:{port}（{summary['ok_count']}/{summary['ok_count'] + summary['fail_count']} 成功）"]
    for field, label in (("dns_ms", "DNS"), ("tcp_ms", "TCP"), ("tls_ms", "TLS"), ("total_ms", "总计")):
        item = summary.get(field) or {}
        if item.get("avg") is None:
            continue
        lines.append(f"  {label:4s} avg {item['avg']:7.1f}ms   p95 {item['p95']:7.1f}ms   max {item['max']:7.1f}ms")
    for error in summary.get("errors") or []:
        lines.append(f"  ❌ {error}")
    return "\n".join(lines)

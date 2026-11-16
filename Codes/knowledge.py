# -*- coding: utf-8 -*-
"""
学习记忆（KnowledgeBase）—— 学习层写，决策层读。

存什么：
- rules：游戏规则/机制结论（合成、掉落、工具、生物、维度、接口行为……）
- skills：验证过的技能用法与注意事项
- qa：学习过程中问过用户的问题与回答
- experiments：做过的实验（技能、参数、结果、背包变化）

为什么单独存：
- 学习层（`python main.py learn`）负责“把规则学明白”，不冲分；
- 决策层（`python main.py run`）读同一份 memory，按用户写的 strategy 打球；
- 两个进程/两次运行之间不丢，赛前 pretraining 的成果可以直接用。

文件：
- `Codes/state/knowledge.json`：机器可读（已 gitignore）
- `Codes/state/knowledge.md`：人类可读导出
"""

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "state", "knowledge.json")
DEFAULT_MARKDOWN = os.path.join(_HERE, "state", "knowledge.md")

MAX_RULES = 400
MAX_SKILLS = 200
MAX_QA = 200
MAX_EXPERIMENTS = 300
MAX_EPISODES = 100


def _now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _empty():
    return {
        "version": 1,
        "updated_at": 0.0,
        "rules": [],
        "skills": [],
        "qa": [],
        "experiments": [],
        "episodes": [],
    }


class KnowledgeBase:
    def __init__(self, path=None, markdown_path=None):
        self.path = path or DEFAULT_PATH
        self.markdown_path = markdown_path or DEFAULT_MARKDOWN
        self.data = _empty()
        self.load(force=True)

    # ================= 读写 =================
    def load(self, force=False):
        if not force and not os.path.isfile(self.path):
            return self.data
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError):
            return self.data
        if not isinstance(loaded, dict):
            return self.data
        data = _empty()
        for key in ("rules", "skills", "qa", "experiments", "episodes"):
            value = loaded.get(key)
            if isinstance(value, list):
                data[key] = [x for x in value if isinstance(x, dict)]
        data["updated_at"] = float(loaded.get("updated_at") or 0)
        self.data = data
        return self.data

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.data["updated_at"] = time.time()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        return self.path

    # ================= 写入 =================
    def add_rule(self, topic, rule, source="llm", evidence=None):
        topic = str(topic or "").strip()[:120]
        rule = str(rule or "").strip()
        if not rule:
            return None
        for old in self.data["rules"]:
            if old.get("topic") == topic and old.get("rule") == rule:
                return old
        item = {
            "topic": topic or "未分类",
            "rule": rule[:2000],
            "source": str(source or "llm"),
            "evidence": str(evidence or "")[:500],
            "at": _now_iso(),
        }
        self.data["rules"].append(item)
        del self.data["rules"][:-MAX_RULES]
        self.save()
        return item

    def add_skill(self, name, notes, args=None, steps=None):
        name = str(name or "").strip()[:80]
        notes = str(notes or "").strip()
        if not name or not notes:
            return None
        item = {
            "name": name,
            "notes": notes[:1000],
            "args": args if isinstance(args, dict) else {},
            "at": _now_iso(),
        }
        if isinstance(steps, list) and steps:
            item["steps"] = steps[:30]
        self.data["skills"].append(item)
        del self.data["skills"][:-MAX_SKILLS]
        self.save()
        return item

    def add_qa(self, question, answer, episode_id=None):
        question = str(question or "").strip()[:500]
        answer = str(answer or "").strip()[:2000]
        if not question or not answer:
            return None
        item = {"q": question, "a": answer, "at": _now_iso()}
        if episode_id:
            item["episode_id"] = str(episode_id)
        self.data["qa"].append(item)
        del self.data["qa"][:-MAX_QA]
        self.save()
        return item

    def add_experiment(self, skill, args, ok, summary, delta=None,
                       context=None, episode_id=None):
        item = {
            "skill": str(skill or "")[:80],
            "args": args if isinstance(args, dict) else {},
            "ok": bool(ok),
            "summary": str(summary or "")[:500],
            "delta": delta if isinstance(delta, dict) else {},
            "at": _now_iso(),
        }
        if context:
            item["context"] = context if isinstance(context, dict) else {"text": str(context)[:500]}
        if episode_id:
            item["episode_id"] = str(episode_id)
        self.data["experiments"].append(item)
        del self.data["experiments"][:-MAX_EXPERIMENTS]
        self.save()
        return item

    # ================= 情境（episode）=================
    def start_episode(self, context=None):
        episode = {
            "id": "ep-" + time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex(),
            "started_at": _now_iso(),
            "context": context if isinstance(context, dict) else {"text": str(context or "")[:500]},
            "turns": [],
            "lesson": "",
        }
        self.data.setdefault("episodes", []).append(episode)
        del self.data["episodes"][:-MAX_EPISODES]
        self.save()
        return episode

    def add_turn(self, episode_id, turn):
        for episode in reversed(self.data.get("episodes") or []):
            if episode.get("id") == episode_id:
                item = dict(turn or {})
                item["at"] = _now_iso()
                episode.setdefault("turns", []).append(item)
                self.save()
                return item
        return None

    def close_episode(self, episode_id, lesson=""):
        for episode in reversed(self.data.get("episodes") or []):
            if episode.get("id") == episode_id:
                episode["ended_at"] = _now_iso()
                episode["lesson"] = str(lesson or "")[:1000]
                self.save()
                return episode
        return None

    # ================= 给提示词看的摘要 =================
    def summary(self, max_rules=25, max_skills=12, max_qa=8, max_chars=2600):
        lines = []
        episodes = self.data.get("episodes") or []
        if episodes:
            last = episodes[-1]
            context = last.get("context") or {}
            lines.append("【最近情境】")
            lines.append(f"- 情境：{str(context)[:300]}")
            for turn in (last.get("turns") or [])[-4:]:
                kind = turn.get("kind") or "?"
                if kind == "ask":
                    lines.append(f"- 问：{turn.get('question')} → 答：{turn.get('answer')}")
                elif kind == "experiment":
                    mark = "成功" if turn.get("ok") else "失败"
                    lines.append(f"- 做：{turn.get('skill')}{turn.get('args') or {}} → {mark}：{turn.get('summary')}")
                else:
                    lines.append(f"- {kind}：{turn.get('text') or turn.get('rule') or ''}")
        rules = self.data.get("rules") or []
        if rules:
            lines.append("【已学规则】")
            for item in rules[-max_rules:]:
                lines.append(f"- [{item.get('topic')}] {item.get('rule')}")
        skills = self.data.get("skills") or []
        if skills:
            lines.append("【已验证技能】")
            for item in skills[-max_skills:]:
                lines.append(f"- {item.get('name')}：{item.get('notes')}")
        qa = self.data.get("qa") or []
        if qa:
            lines.append("【用户确认】")
            for item in qa[-max_qa:]:
                lines.append(f"- 问：{item.get('q')} 答：{item.get('a')}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n……（memory 太长，已截断）"
        return text

    def counts(self):
        return {
            "rules": len(self.data.get("rules") or []),
            "skills": len(self.data.get("skills") or []),
            "qa": len(self.data.get("qa") or []),
            "experiments": len(self.data.get("experiments") or []),
            "episodes": len(self.data.get("episodes") or []),
        }

    def reset(self):
        """清空学习记忆（保留文件结构）。"""
        self.data = _empty()
        self.save()
        self.export_markdown()
        return self.counts()

    # ================= 人类可读导出 =================
    def export_markdown(self, path=None):
        path = path or self.markdown_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = self.data
        lines = [
            "# 学习记忆（Knowledge Base）",
            "",
            f"> 更新时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 统计：规则 {len(data['rules'])} 条，技能 {len(data['skills'])} 条，"
            f"问答 {len(data['qa'])} 条，实验 {len(data['experiments'])} 条",
            "",
            "## 游戏规则",
            "",
        ]
        if data["rules"]:
            for item in data["rules"]:
                lines.append(f"- **[{item.get('topic')}]** {item.get('rule')}")
                if item.get("evidence"):
                    lines.append(f"  - 依据：{item.get('evidence')}")
        else:
            lines.append("（还没有，去 `python main.py learn` 学）")

        lines += ["", "## 已验证技能", ""]
        if data["skills"]:
            for item in data["skills"]:
                lines.append(f"- **{item.get('name')}**：{item.get('notes')}")
        else:
            lines.append("（还没有）")

        lines += ["", "## 用户问答", ""]
        if data["qa"]:
            for item in data["qa"]:
                lines.append(f"- **问**：{item.get('q')}")
                lines.append(f"  **答**：{item.get('a')}")
        else:
            lines.append("（还没有）")

        lines += ["", "## 学习情境（最近 10 段）", ""]
        if data.get("episodes"):
            for ep in data["episodes"][-10:]:
                lines.append(f"### {ep.get('id')}（{ep.get('started_at')}）")
                lines.append(f"- 情境：`{json.dumps(ep.get('context') or {}, ensure_ascii=False)}`")
                for turn in ep.get("turns") or []:
                    kind = turn.get("kind")
                    if kind == "ask":
                        lines.append(f"  - 问：{turn.get('question')}")
                        lines.append(f"    答：{turn.get('answer')}")
                    elif kind == "experiment":
                        mark = "✅" if turn.get("ok") else "❌"
                        lines.append(f"  - {mark} `{turn.get('skill')}` → {turn.get('summary')}")
                    else:
                        lines.append(f"  - {kind}：{turn.get('text') or turn.get('rule') or ''}")
                if ep.get("lesson"):
                    lines.append(f"- 结论：{ep.get('lesson')}")
                lines.append("")
        else:
            lines.append("（还没有）")

        lines += ["", "## 实验记录（最近 30 条）", ""]
        if data["experiments"]:
            for item in data["experiments"][-30:]:
                mark = "✅" if item.get("ok") else "❌"
                lines.append(
                    f"- {mark} `{item.get('skill')}` {json.dumps(item.get('args') or {}, ensure_ascii=False)}"
                    f" → {item.get('summary')}"
                )
                if item.get("delta"):
                    lines.append(f"  - 背包变化：`{json.dumps(item.get('delta'), ensure_ascii=False)}`")
        else:
            lines.append("（还没有）")

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

# -*- coding: utf-8 -*-
"""
====================================================================
 配置中心 —— 想改 AI 的行为，基本都在这个文件里改。

 密钥、账号密码不写在这里，写在同目录的 `.env` 文件里（已 gitignore）。
 本文件只负责「读配置 + 给默认值」，不含任何私密信息，可以安全提交。
====================================================================
"""

import os


# ---------------------------------------------------------------
# 一、先读 .env（不需要装 python-dotenv，自己解析）
# ---------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _load_dotenv(path):
    """把 .env 里的 KEY=VALUE 读进环境变量。已存在的环境变量优先，不覆盖。"""
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_dotenv(os.path.join(_HERE, ".env"))     # Codes/.env
_load_dotenv(os.path.join(_ROOT, ".env"))     # 项目根/.env（可选）


def _env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name, default):
    try:
        return int(str(_env(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(str(_env(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    value = str(_env(name, "")).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


# ---------------------------------------------------------------
# 二、大模型（大脑）
# ---------------------------------------------------------------
# ⚠️ 比赛必须用 AIA 统一代理。拿到地址后改 Codes/.env 里的 LLM_* 三行即可。
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = _env("LLM_API_KEY", "")

LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.2)   # 选动作要稳，别太随机
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 500)       # 一次决策够用
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 60)
LLM_RETRIES = _env_int("LLM_RETRIES", 4)                # 429/5xx 自动退避重试
LLM_JSON_MODE = _env_bool("LLM_JSON_MODE", True)        # 代理支持 response_format 就开


# ---------------------------------------------------------------
# 三、Token 预算（比赛硬限额，按代理返回的 usage 统计）
# ---------------------------------------------------------------
INPUT_TOKEN_BUDGET = _env_int("INPUT_TOKEN_BUDGET", 8_000_000)     # 初赛 800 万
OUTPUT_TOKEN_BUDGET = _env_int("OUTPUT_TOKEN_BUDGET", 1_500_000)   # 初赛 150 万
BUDGET_WARN_RATIO = 0.70     # 到 70% 开始提示
BUDGET_STOP_RATIO = 0.98     # 到 98% 主动收手，保存状态


# ---------------------------------------------------------------
# 四、赛事 Bot 接口（NMO）
# ---------------------------------------------------------------
# 后端选择：
#   nmo       = 比赛服的 NMO 接口（默认）
#   my_server = 你自己的服务器（Codes/my_server/ 适配器；局域网测试用这个）
BACKEND = _env("BACKEND", "nmo").strip().lower()

MC_API_BASE = _env("MC_API_BASE", "https://mc-api.windywindie.top/api/v1")
MC_USERNAME = _env("MC_USERNAME", "")
MC_PASSWORD = _env("MC_PASSWORD", "")
MC_TIMEOUT = _env_int("MC_TIMEOUT", 90)      # 单个动作最长等多久（秒）


# ---------------------------------------------------------------
# 五、运行方式
# ---------------------------------------------------------------
# 比赛结束时间（当天几点）——到点自动收手并保存最终进度
COMPETITION_END = _env("COMPETITION_END", "18:00")

# 想只跑一段时间（调试用）：分钟数，0 表示按 COMPETITION_END 算
RUN_MINUTES = _env_int("RUN_MINUTES", 0)

# 最多决策多少轮（防止死循环；正常跑不到）
MAX_TICKS = _env_int("MAX_TICKS", 200000)
LEARN_MAX_TICKS = _env_int("LEARN_MAX_TICKS", 200)

# 两次「问大脑」之间至少隔几秒（0 = 不限制）
DECISION_MIN_INTERVAL = _env_float("DECISION_MIN_INTERVAL", 0)

# 多久刷新一次 advancement 进度（秒）
ADVANCEMENT_REFRESH_S = _env_float("ADVANCEMENT_REFRESH_S", 90)

# 网络延迟告警阈值（毫秒）：状态接口/大模型单次超过它就打日志提示
NET_LATENCY_WARN_MS = _env_int("NET_LATENCY_WARN_MS", 3000)

# 是否在控制台逐条打印每次交互的延迟（API/LLM）
PRINT_LATENCY = _env_bool("PRINT_LATENCY", True)

# 聊天模式：每条回复最多多少字、轮询间隔、冷却
CHAT_MAX_CHARS = _env_int("CHAT_MAX_CHARS", 25)
CHAT_POLL_S = _env_float("CHAT_POLL_S", 1.5)
CHAT_COOLDOWN_S = _env_float("CHAT_COOLDOWN_S", 1.2)

# 背包缓存多久（秒）——太短会多请求，太长会拿旧数据
INVENTORY_TTL_S = _env_float("INVENTORY_TTL_S", 6)

# 运行数据放哪（相对 Codes/）
STATE_DIR = _env("STATE_DIR", "state")
LOG_DIR = _env("LOG_DIR", "logs")

# 策略文件（相对 Codes/；改这个文件即可换打法，下一个决策生效）
STRATEGY_FILE = _env("STRATEGY_FILE", os.path.join("data", "strategy.json"))
STRATEGY_DOC = _env("STRATEGY_DOC", os.path.join("data", "strategy.md"))

# 任务记录（给人看的总账）
TASK_LOG_FILE = _env("TASK_LOG_FILE", "任务记录.md")
TASK_LOG_AT_ROOT = _env_bool("TASK_LOG_AT_ROOT", True)   # 写到项目根目录

# 调试输出（True 会打印更多细节）
DEBUG = _env_bool("DEBUG", False)


# ---------------------------------------------------------------
# 六、安全（保命优先，别触发「死亡循环保护」）
# ---------------------------------------------------------------
EAT_BELOW_FOOD = _env_int("EAT_BELOW_FOOD", 14)          # 饥饿低于这个值就吃东西
EAT_BELOW_HEALTH = _env_int("EAT_BELOW_HEALTH", 16)      # 血量低于这个值也吃东西
FLEE_BELOW_HEALTH = _env_int("FLEE_BELOW_HEALTH", 6)     # 血量低于这个值、附近有怪就跑
FLEE_DISTANCE = _env_int("FLEE_DISTANCE", 18)            # 逃跑跑多远

# 死亡循环保护：60 秒内死 3 次会被强制下线 5 分钟，我们要主动避让
DEATH_WINDOW_S = _env_int("DEATH_WINDOW_S", 60)
DEATH_LIMIT = _env_int("DEATH_LIMIT", 3)
SLEEP_AFTER_DEATH_LOOP_S = _env_int("SLEEP_AFTER_DEATH_LOOP_S", 300)

# 同一个技能+参数连续失败几次就拉黑一段时间
FAIL_BLACKLIST_COUNT = _env_int("FAIL_BLACKLIST_COUNT", 2)
FAIL_BLACKLIST_SECONDS = _env_int("FAIL_BLACKLIST_SECONDS", 120)


# ---------------------------------------------------------------
# 七、以后接「你自己的服务器」
# ---------------------------------------------------------------
# 在 Codes/.env 里填 MY_SERVER_BASE / MY_SERVER_TOKEN，
# 然后去 main.py 的 build_backend() 里把注释打开即可（适配器在 Codes/my_server/）。
MY_SERVER_BASE = _env("MY_SERVER_BASE", "")
MY_SERVER_TOKEN = _env("MY_SERVER_TOKEN", "")
MY_SERVER_TIMEOUT = _env_int("MY_SERVER_TIMEOUT", 30)


# ---------------------------------------------------------------
# 八、兼容旧名字（旧脚本/旧文档里可能还在用，新代码不要用）
# ---------------------------------------------------------------
API_KEY = LLM_API_KEY
API_BASE_URL = LLM_BASE_URL
MODEL = LLM_MODEL

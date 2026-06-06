from pathlib import Path

BASE_DIR = Path(__file__).parent.parent   # job-hunter/（src/ 的上一层）

# ── Directories ──────────────────────────────────────────────────────────────
RESUME_DIR            = BASE_DIR / "resume"
SCREENSHOTS_DIR       = BASE_DIR / "screenshots"
RECORDS_DIR           = BASE_DIR / "records"
LOGS_DIR              = BASE_DIR / "logs"
OUTPUT_RESUMES_DIR    = BASE_DIR / "output_resumes"
BROWSER_DATA_DIR      = BASE_DIR / "browser_data"
BROWSER_DATA_CHAT_DIR = BASE_DIR / "browser_data_chat"

for _d in [RESUME_DIR, SCREENSHOTS_DIR, RECORDS_DIR, LOGS_DIR,
           OUTPUT_RESUMES_DIR, BROWSER_DATA_DIR, BROWSER_DATA_CHAT_DIR]:
    _d.mkdir(exist_ok=True)

# ── CDP 调试端口 ──────────────────────────────────────────────────────────────
CDP_SCANNER_PORT = 9222                              # job_scanner.py 专用
CDP_CHAT_PORT    = 9223                              # chat_handler.py 专用
CDP_SCANNER_URL  = f"http://localhost:{CDP_SCANNER_PORT}"
CDP_CHAT_URL     = f"http://localhost:{CDP_CHAT_PORT}"

# ── AI API (OpenAI-compatible proxy) ─────────────────────────────────────────
# 真实值存放在 confidential.py（已加入 .gitignore，不上传）
API_BASE_URL = "***"
API_KEY      = "***"
AI_MODEL     = "***"

try:
    from confidential import API_BASE_URL, API_KEY, AI_MODEL  # noqa: F811
except ImportError:
    pass  # CI / 首次克隆时占位符生效，运行前请创建 src/confidential.py

# ── Matching ──────────────────────────────────────────────────────────────────
SCORE_THRESHOLD = 70

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH     = RECORDS_DIR / "jobs.db"
RESUME_PATH = RESUME_DIR  / "袁柯.pdf"
LOG_PATH    = LOGS_DIR    / "app.log"

# ── Scanner 行为开关 ──────────────────────────────────────────────────────────
SCAN_API_ENABLED   = False  # True=调用 AI API 分析匹配度；False=跳过，score=0
SCAN_GREET_ENABLED = False  # True=点击「立即沟通」并处理弹窗；False=只扫描不打招呼

# ── 聊天模块运行参数 ──────────────────────────────────────────────────────────
POLL_LIMIT        = 1    # 单轮最多处理会话数（调试=1，生产=50）
CHAT_MAX_AGE_DAYS = 100  # 超过此天数的会话跳过并重头轮询
# True=持续轮询（生产模式）；False=处理完一轮后退出，且不点击左侧会话卡片
CONTINUOUS_POLL   = False
# True=正常回复（发固定话术/API回复）；False=只做卡片同意和发简历，不产生新消息
REPLY_ENABLED     = True
# True=允许点击发送按钮；False=只打入输入框，不点击发送（REPLY_ENABLED=False时此开关无效）
SEND_ENABLED      = False

# ── 聊天固定话术 ──────────────────────────────────────────────────────────────
FIXED_SELF_INTRO = (
    "您好！感谢您主动联系我。"
    "我有5年数据分析经验，曾在滴滴负责指标体系建设、异动归因分析和A/B实验设计，"
    "熟练使用Python、SQL/HiveSQL和Tableau，有丰富的业务支持与管理层汇报经历。"
    "很期待进一步了解贵司岗位详情，希望有机会深入沟通！"
)

FIXED_FOLLOWUP = (
    "您好！之前我主动发起了沟通，希望了解贵司的相关岗位机会。"
    "我有5年数据分析经验，擅长指标体系建设、异动归因分析和A/B实验设计，"
    "熟练使用Python、SQL/HiveSQL和Tableau，有丰富的业务支持与管理层汇报经历。"
    "如方便的话，期待能进一步交流！"
)

DISCLAIMER = ""     # 消息末尾免责声明（暂时置空，正式使用时填入）

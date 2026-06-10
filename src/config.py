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
CDP_CHAT_PORT    = 9222                              # chat_handler.py 专用
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
SCAN_API_ENABLED   = True  # True=调用 AI API 分析匹配度；False=跳过，score=0
SCAN_GREET_ENABLED = True  # True=点击「立即沟通」并处理弹窗；False=只扫描不打招呼
MAX_NEW_JOBS       = 50   # 单次运行最多处理的新岗位数（数据库中无记录的）

# ── 聊天模块运行参数 ──────────────────────────────────────────────────────────
POLL_LIMIT        = 10    # 单轮最多处理会话数（调试=1，生产=50）
# True=持续轮询（生产模式）；False=处理完一轮后退出，且不点击左侧会话卡片
CONTINUOUS_POLL   = True
# True=正常回复（发固定话术/API回复）；False=只做卡片同意和发简历，不产生新消息
REPLY_ENABLED     = False
# True=允许点击发送按钮；False=只打入输入框，不点击发送（REPLY_ENABLED=False时此开关无效）
SEND_ENABLED      = False

DISCLAIMER = "（你好，我目前不在线，该消息为自动发送，稍后与您联系）"     # 消息末尾免责声明（暂时置空，正式使用时填入）

# True=按 JD 用 AI 生成定制简历并作为新附件上传发送（用后删除）；False=发送固定的 resume/袁柯.pdf
GENERATE_TAILORED_RESUME = False

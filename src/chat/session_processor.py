import sys, json, re, sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pdfplumber

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (DB_PATH, RESUME_PATH, CHAT_MAX_AGE_DAYS)
from shared.cdp_utils import evaluate, read_messages
from shared.database import (get_chat, upsert_chat, save_job_from_chat,
                              get_job_by_encrypt_id)
from shared.logger import log
from chat.session_actions import execute_session_actions

# ── 简历缓存 ──────────────────────────────────────────────────────────────────

_resume_cache: str = ""

# ── JS 片段 ───────────────────────────────────────────────────────────────────

_JS_CHAT_INFO = """
(function() {
    try {
        const c = window.chat && window.chat.communicating;
        if (!c) return null;
        return JSON.stringify({
            encryptJobId : c.encryptJobId  || '',
            companyName  : c.companyName   || '',
            title        : c.title         || '',
            name         : c.name          || '',
            bothTalked   : !!c.bothTalked,
            jobName      : c.jobName       || '',
            salaryDesc   : c.salaryDesc    || '',
            locationName : c.locationName  || '',
        });
    } catch(e) { return null; }
})()
"""

# ── 读取函数 ──────────────────────────────────────────────────────────────────

def get_current_chat_info(tab) -> dict:
    val = evaluate(tab, _JS_CHAT_INFO)
    if val and isinstance(val, str) and val != "null":
        try:
            return json.loads(val)
        except Exception:
            pass
    return {}


def load_resume() -> str:
    global _resume_cache
    if _resume_cache:
        return _resume_cache
    txt = RESUME_PATH.with_suffix(".txt")
    if txt.exists():
        _resume_cache = txt.read_text(encoding="utf-8")
        return _resume_cache
    if RESUME_PATH.exists():
        with pdfplumber.open(str(RESUME_PATH)) as pdf:
            _resume_cache = "\n".join(p.extract_text() or "" for p in pdf.pages)
        txt.write_text(_resume_cache, encoding="utf-8")
        return _resume_cache
    log.warning("  [简历] 未找到简历文件")
    return ""


# ── 时间判断 ──────────────────────────────────────────────────────────────────

def is_session_too_old(time_str: str) -> bool:
    """
    判断会话最新消息是否超过 CHAT_MAX_AGE_DAYS 天。
    time_str 来自左侧会话卡片的 .time 字段：
      今天   → "14:53"  (HH:MM)
      昨天   → "昨天"
      本周   → "周一"~"周日"
      更早   → "06/01" / "6月1日" 等日期格式
    """
    if not time_str:
        return False
    t = time_str.strip()

    if re.match(r'^\d{1,2}:\d{2}$', t):
        return False
    if '昨天' in t:
        return False
    if '周' in t or '星期' in t:
        return CHAT_MAX_AGE_DAYS < 7

    cutoff = datetime.now() - timedelta(days=CHAT_MAX_AGE_DAYS)

    m = re.match(r'^(\d{1,2})[/-](\d{1,2})$', t)
    if m:
        try:
            d = datetime(datetime.now().year, int(m.group(1)), int(m.group(2)))
            return d < cutoff
        except ValueError:
            return False

    m = re.match(r'^(\d{1,2})月(\d{1,2})日?$', t)
    if m:
        try:
            d = datetime(datetime.now().year, int(m.group(1)), int(m.group(2)))
            return d < cutoff
        except ValueError:
            return False

    return False


def get_session_time(tab, chat_info: dict) -> str:
    """从左侧会话列表找到当前 boss 对应的 li，读取其 .time 字段。"""
    boss_name = chat_info.get("name", "")
    js = f"""
    (function() {{
        const lis = Array.from(document.querySelectorAll(
            '.user-list-content > ul:nth-child(2) > li'
        ));
        const target = {json.dumps(boss_name)};
        for (const li of lis) {{
            const nameEl = li.querySelector('.name-text');
            if (!nameEl) continue;
            if (!target || (nameEl.innerText||'').trim() === target) {{
                const timeEl = li.querySelector('.time');
                return timeEl ? (timeEl.innerText||'').trim() : '';
            }}
        }}
        return '';
    }})()
    """
    val = evaluate(tab, js)
    return val if isinstance(val, str) else ''


# ── 单个会话处理 ──────────────────────────────────────────────────────────────

def process_session(tab, session_info: dict | None = None):
    """
    处理当前可见会话。执行顺序：读取 → 判断 → 写库 → 操作，操作后不再写库。
    session_info: 来自左侧会话卡片的基本信息（可为 None）。
    """
    label = session_info["name"] if session_info else "当前会话"
    log.info(f"{'='*60}")
    log.info(f"  处理会话：{label}")
    log.info(f"{'='*60}")

    try:
        # ══════════════════════════════════════════════════════════════
        # 阶段一：读取与分析（无副作用）
        # ══════════════════════════════════════════════════════════════

        # 1. 读取会话基本信息
        chat_info      = get_current_chat_info(tab)
        encrypt_job_id = chat_info.get("encryptJobId", "")
        company        = chat_info.get("companyName", "") or (session_info or {}).get("company", "")
        boss_title     = chat_info.get("title",       "") or (session_info or {}).get("title",   "")
        boss_name      = chat_info.get("name",        "") or (session_info or {}).get("name",    "")
        log.info(f"  公司: {company}  Boss: {boss_name}({boss_title})")
        log.info(f"  encryptJobId: {encrypt_job_id or '(未读到)'}")

        # 2. 时间检查
        session_time = (session_info or {}).get("time", "") or get_session_time(tab, chat_info)
        if is_session_too_old(session_time):
            log.info(f"  → 最新消息 {session_time!r} 超过一周，跳过")
            return
        log.info(f"  → 最新消息时间: {session_time!r}（一周内）")

        # 3. 查岗位表
        job_row    = get_job_by_encrypt_id(encrypt_job_id) if encrypt_job_id else None
        jobs_db_id = job_row["id"] if job_row else 0
        jd         = job_row["jd"] if job_row else ""
        if job_row:
            log.info(f"  匹配到岗位 id={jobs_db_id}: {job_row['position']}")
        else:
            log.info("  [岗位] 未匹配到 jobs 表，无 JD 上下文")

        # 4. 一次性读取消息（此后不再调用 read_messages）
        messages = read_messages(tab)
        log.info(f"  消息数量: {len(messages)}")
        if not messages:
            log.info("  [跳过] 聊天记录为空")
            return

        # 5. 分类与计算
        my_texts   = [m for m in messages if     m["isSelf"]  and not m["isCard"]]
        boss_texts = [m for m in messages if not m["isSelf"]  and not m["isSystem"] and not m["isCard"]]
        has_jd     = bool(job_row and jd.strip())
        initiator  = "boss" if (not my_texts and boss_texts) else "me"
        log.info(f"  我方文字: {len(my_texts)} 条  Boss文字: {len(boss_texts)} 条")

        # last_is_boss：从末尾找第一条非系统、非卡片、有文字的消息，判断是否来自 boss
        _last_text_msg = next(
            (m for m in reversed(messages)
             if not m["isSystem"] and not m["isCard"] and m.get("text")),
            None
        )
        last_is_boss = _last_text_msg is not None and not _last_text_msg["isSelf"]

        # 6. 简历状态检测（纯读取，不操作）
        existing       = get_chat(encrypt_job_id) if encrypt_job_id else None
        db_resume_sent = bool((existing or {}).get("resume_sent", 0))

        if db_resume_sent:
            resume_already_sent = True
            log.info("  → 数据库：简历已投递过")
        elif not boss_texts:
            resume_already_sent = False
        elif not my_texts:
            resume_already_sent = False
        else:
            resume_already_sent = any(
                m.get("isSystem") and "简历" in m.get("text", "")
                for m in messages
            )
            if resume_already_sent:
                log.info("  → 系统消息：简历已发送过")

        log.info(f"  简历状态: already_sent={resume_already_sent}")

        # 7. 加载简历
        resume = load_resume()

        # 8. 无JD 时补录岗位
        if not has_jd and not job_row:
            saved_id = save_job_from_chat(chat_info)
            if saved_id:
                jobs_db_id = saved_id
                log.info(f"  → 岗位已保存至 jobs 表 id={saved_id}（source=chat）")

        # ══════════════════════════════════════════════════════════════
        # 阶段二：写库（操作前状态，仅此一次）
        # ══════════════════════════════════════════════════════════════

        if encrypt_job_id:
            history_list = [
                {
                    "from"  : ("me"     if m["isSelf"]
                               else "system" if m["isSystem"]
                               else "boss"),
                    "text"  : m["text"],
                    "time"  : m["time"],
                    "status": m.get("status", ""),
                    "isCard": m["isCard"],
                }
                for m in messages
            ]
            upsert_chat(
                encrypt_job_id = encrypt_job_id,
                jobs_db_id     = jobs_db_id,
                boss_name      = boss_name,
                company        = company,
                boss_title     = boss_title,
                initiator      = initiator,
                chat_history   = history_list,
                resume_sent    = 1 if resume_already_sent else 0,
                # tendency_score / ai_reasoning 不传，保留库中已有值
            )
            log.info(f"  [DB] 已写入当前状态（resume_sent={1 if resume_already_sent else 0}）")
        else:
            log.info("  [DB] 无 encryptJobId，跳过写库")

        # ══════════════════════════════════════════════════════════════
        # 阶段三：执行操作（委托给 session_actions）
        # ══════════════════════════════════════════════════════════════

        execute_session_actions(
            tab                 = tab,
            has_jd              = has_jd,
            my_texts            = my_texts,
            boss_texts          = boss_texts,
            last_is_boss        = last_is_boss,
            resume_already_sent = resume_already_sent,
            resume              = resume,
            jd                  = jd,
            chat_info           = chat_info,
            messages            = messages,
        )

    except Exception as e:
        log.exception(f"  [错误] 会话 {label} 处理异常，已跳过: {e}")

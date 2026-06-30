import sys, json
from pathlib import Path
import pdfplumber

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import RESUME_PATH, SELF_PROMO_TEXT
from shared.cdp_utils import evaluate, read_messages
from shared.database import (get_chat, upsert_chat, save_job_from_view_detail,
                              get_job_by_encrypt_id)
from shared.logger import log
from chat.session_actions import execute_session_actions, fetch_job_detail_via_view_job

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

        # encryptJobId 正常情况下必定能读到；读不到说明页面结构异常或选择器失效，
        # 后续 JD 获取/匹配/写库全部依赖该字段，继续运行没有意义，直接终止程序排查
        if not encrypt_job_id:
            log.error(f"  [致命错误] 未读到 encryptJobId（会话：{label}），"
                      f"该字段正常情况下必定存在，可能是页面结构变化导致选择器失效，程序退出")
            sys.exit(1)

        # 2. 查岗位表
        job_row    = get_job_by_encrypt_id(encrypt_job_id) if encrypt_job_id else None
        jobs_db_id = job_row["id"] if job_row else 0
        jd         = job_row["jd"]     if job_row else ""
        salary     = job_row["salary"] if job_row else ""
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
        initiator  = "boss" if (not my_texts and boss_texts) else "me"
        log.info(f"  我方文字: {len(my_texts)} 条  Boss文字: {len(boss_texts)} 条")

        # last_is_boss：从末尾找第一条非系统、非卡片、有文字的消息，判断是否来自 boss
        _last_text_msg = next(
            (m for m in reversed(messages)
             if not m["isSystem"] and not m["isCard"] and m.get("text")),
            None
        )
        last_is_boss = _last_text_msg is not None and not _last_text_msg["isSelf"]

        # 6. 简历状态 & 自我介绍状态检测（纯读取，不操作）
        existing              = get_chat(encrypt_job_id) if encrypt_job_id else None
        db_resume_sent        = bool((existing or {}).get("resume_sent", 0))
        db_sent_self_promo    = bool((existing or {}).get("sent_self_promo", 0))

        if db_resume_sent:
            resume_already_sent = True
            log.info("  → 数据库：简历已投递过")
        else:
            resume_already_sent = any(
                m.get("isSystem") and "简历" in m.get("text", "")
                for m in messages
            )
            if resume_already_sent:
                log.info("  → 系统消息：简历已发送过")

        # 自我介绍前缀（取固定文案前 30 字做子串匹配，避免 DOM 截断误差）
        _promo_prefix = SELF_PROMO_TEXT[:30]
        if db_sent_self_promo:
            self_promo_already_sent = True
            log.info("  → 数据库：自我介绍已发送过")
        else:
            self_promo_already_sent = any(
                _promo_prefix in m.get("text", "")
                for m in messages if m["isSelf"] and not m["isCard"]
            )
            if self_promo_already_sent:
                log.info("  → DOM检测：自我介绍已发送过")

        log.info(f"  简历状态: already_sent={resume_already_sent}"
                 f"  self_promo_already_sent={self_promo_already_sent}")

        # 7. 加载简历
        resume = load_resume()

        # 8. 未匹配到岗位记录时，通过「查看职位」打开详情页补录完整岗位信息
        #    （encrypt_job_id 已在上方保证非空，该耗时操作通过 not job_row 确保每个岗位只触发一次）
        if not job_row:
            detail = fetch_job_detail_via_view_job(tab)
            if detail and detail.get("jd"):
                saved_id = save_job_from_view_detail(encrypt_job_id, detail)
                if saved_id:
                    jobs_db_id = saved_id
                    jd         = detail["jd"]
                    salary     = detail.get("salary", "")
                    log.info(f"  → 通过「查看职位」补录岗位 id={saved_id}"
                             f"（source=chat, JD {len(jd)} 字）")

            # 兜底：补录后仍无 JD（按钮缺失/详情页超时/JD 为空等），
            # 本轮无法获得任何岗位上下文，结束该会话处理，留待下轮重试
            if not jd:
                log.info("  [跳过] 未匹配到岗位记录且「查看职位」未获取到 JD，结束本轮处理")
                return

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
                encrypt_job_id  = encrypt_job_id,
                jobs_db_id      = jobs_db_id,
                boss_name       = boss_name,
                company         = company,
                boss_title      = boss_title,
                initiator       = initiator,
                chat_history    = history_list,
                resume_sent     = 1 if resume_already_sent else 0,
                sent_self_promo = 1 if self_promo_already_sent else 0,
            )
            log.info(f"  [DB] 已写入当前状态（resume_sent={1 if resume_already_sent else 0}"
                     f"  sent_self_promo={1 if self_promo_already_sent else 0}）")
        else:
            log.info("  [DB] 无 encryptJobId，跳过写库")

        # ══════════════════════════════════════════════════════════════
        # 阶段三：执行操作（委托给 session_actions）
        # ══════════════════════════════════════════════════════════════

        execute_session_actions(
            tab                      = tab,
            my_texts                 = my_texts,
            boss_texts               = boss_texts,
            last_is_boss             = last_is_boss,
            resume_already_sent      = resume_already_sent,
            self_promo_already_sent  = self_promo_already_sent,
            resume                   = resume,
            jd                       = jd,
            salary                   = salary,
            chat_info                = chat_info,
            messages                 = messages,
        )

    except Exception as e:
        log.exception(f"  [错误] 会话 {label} 处理异常，已跳过: {e}")

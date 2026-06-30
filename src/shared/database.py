import sys
import sqlite3
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _add_col(c: sqlite3.Connection, col: str, definition: str):
    """向已存在的表添加列，列已存在时静默忽略。"""
    try:
        c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {definition}")
    except sqlite3.OperationalError:
        pass


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id        TEXT    DEFAULT '',
                company       TEXT    NOT NULL,
                position      TEXT    NOT NULL,
                jd            TEXT    NOT NULL,
                experience    TEXT    DEFAULT '',
                education     TEXT    DEFAULT '',
                company_size  TEXT    DEFAULT '',
                salary        TEXT    DEFAULT '',  -- 解码后的薪资描述（解码失败时存原始乱码）
                salary_ok     INTEGER DEFAULT 0,   -- 薪资解码是否完全成功：0=失败/未知 1=成功
                city          TEXT    DEFAULT '',
                recruiter_name  TEXT  DEFAULT '',  -- 招聘者姓名（来自聊天页「查看职位」详情）
                recruiter_title TEXT  DEFAULT '',  -- 招聘者 title（如"招聘者"）
                greeted       INTEGER DEFAULT 0,   -- 0=未打招呼 1=本次打招呼 2=他端已沟通
                score         INTEGER DEFAULT -1,  -- -1=未分析 0-100=AI 匹配分
                should_apply  INTEGER DEFAULT -1,  -- -1=未分析 0=不推荐 1=推荐投递
                key_matches   TEXT    DEFAULT '',
                missing_skills TEXT   DEFAULT '',
                skip_reason   TEXT    DEFAULT '',
                resume_file   TEXT    DEFAULT '',
                created_at    TEXT    DEFAULT (datetime('now','localtime')),
                updated_at    TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)
        # 向旧版数据库补充新列
        for col, defn in [
            ("job_id",         "TEXT    DEFAULT ''"),
            ("experience",     "TEXT    DEFAULT ''"),
            ("education",      "TEXT    DEFAULT ''"),
            ("company_size",   "TEXT    DEFAULT ''"),
            ("salary",         "TEXT    DEFAULT ''"),
            ("salary_ok",      "INTEGER DEFAULT 0"),
            ("city",           "TEXT    DEFAULT ''"),
            ("recruiter_name",  "TEXT    DEFAULT ''"),
            ("recruiter_title", "TEXT    DEFAULT ''"),
            ("source",         "TEXT    DEFAULT 'scanner'"),
            ("score",          "INTEGER DEFAULT -1"),
            ("should_apply",   "INTEGER DEFAULT -1"),
            ("key_matches",    "TEXT    DEFAULT ''"),
            ("missing_skills", "TEXT    DEFAULT ''"),
            ("skip_reason",    "TEXT    DEFAULT ''"),
            ("resume_file",    "TEXT    DEFAULT ''"),
            ("greeted",       "INTEGER DEFAULT 0"),
        ]:
            _add_col(c, col, defn)
        c.commit()


def save_job(
    *,
    job_id: str,
    company: str,
    position: str,
    jd: str,
    experience: str = "",
    education: str = "",
    company_size: str = "",
    salary: str = "",
    salary_ok: int = 0,
    city: str = "",
    recruiter_name: str = "",
    recruiter_title: str = "",
    greeted: int = 0,
    score: int = -1,
    should_apply: int = -1,
    key_matches: list | None = None,
    missing_skills: list | None = None,
    skip_reason: str = "",
    resume_file: str = "",
    source: str = "scanner",
) -> int:
    """插入一条新岗位记录，返回 rowid。"""
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO jobs
                (job_id, company, position, jd, experience, education, company_size,
                 salary, salary_ok, city, recruiter_name, recruiter_title,
                 greeted, score, should_apply, key_matches, missing_skills, skip_reason,
                 resume_file, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id, company.strip(), position.strip(), jd.strip(),
                experience, education, company_size,
                salary, salary_ok, city, recruiter_name, recruiter_title,
                greeted, score, should_apply,
                json.dumps(key_matches or [], ensure_ascii=False),
                json.dumps(missing_skills or [], ensure_ascii=False),
                skip_reason, resume_file, source,
            ),
        )
        c.commit()
        return cur.lastrowid



# ── chats 表 ──────────────────────────────────────────────────────────────────

def init_chat_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                encrypt_job_id  TEXT    UNIQUE,
                jobs_db_id      INTEGER DEFAULT 0,
                boss_name       TEXT    DEFAULT '',
                company         TEXT    DEFAULT '',
                boss_title      TEXT    DEFAULT '',
                initiator       TEXT    DEFAULT 'me',
                chat_history    TEXT    DEFAULT '[]',
                resume_sent      INTEGER DEFAULT 0,
                sent_self_promo  INTEGER DEFAULT 0,
                created_at       TEXT    DEFAULT (datetime('now','localtime')),
                updated_at      TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)
        # chats 表迁移：补旧库缺少的列
        for col, defn in [
            ("initiator",        "TEXT DEFAULT 'me'"),
            ("sent_self_promo",  "INTEGER DEFAULT 0"),
        ]:
            try:
                c.execute(f"ALTER TABLE chats ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass
        # jobs 表迁移：补 city 和 source
        for col, defn in [
            ("city",   "TEXT DEFAULT ''"),
            ("source", "TEXT DEFAULT 'scanner'"),
        ]:
            try:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass
        c.commit()


def get_chat(encrypt_job_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM chats WHERE encrypt_job_id=?", (encrypt_job_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_chat(
    encrypt_job_id: str,
    jobs_db_id: int = 0,
    boss_name: str = "",
    company: str = "",
    boss_title: str = "",
    initiator: str = "me",
    chat_history: list | None = None,
    resume_sent: int = 0,
    sent_self_promo: int = 0,
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hist_json = json.dumps(chat_history or [], ensure_ascii=False)
    with _conn() as c:
        c.execute("""
            INSERT INTO chats
                (encrypt_job_id, jobs_db_id, boss_name, company, boss_title,
                 initiator, chat_history, resume_sent, sent_self_promo,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(encrypt_job_id) DO UPDATE SET
                jobs_db_id      = excluded.jobs_db_id,
                boss_name       = excluded.boss_name,
                company         = excluded.company,
                boss_title      = excluded.boss_title,
                initiator       = excluded.initiator,
                chat_history    = excluded.chat_history,
                resume_sent     = MAX(resume_sent, excluded.resume_sent),
                sent_self_promo = MAX(sent_self_promo, excluded.sent_self_promo),
                updated_at      = excluded.updated_at
        """, (encrypt_job_id, jobs_db_id, boss_name, company, boss_title,
              initiator, hist_json, resume_sent, sent_self_promo, now, now))
        c.commit()


def save_job_from_view_detail(encrypt_job_id: str, detail: dict) -> int:
    """
    将聊天页「查看职位」打开的详情页中读到的完整岗位信息写入 jobs 表（source='chat'）。
    与旧版 save_job_from_chat 不同，这里能拿到完整 JD，不再写入残缺占位记录。
    若 job_id 已存在则直接返回已有 id，不重复写入。
    返回 jobs.id（失败时返回 0）。
    """
    company  = detail.get("companyName", "")
    position = detail.get("jobName",     "") or "(IM会话，职位未知)"
    jd       = detail.get("jd",          "")
    if not encrypt_job_id or not company or not jd:
        return 0
    with _conn() as c:
        row = c.execute("SELECT id FROM jobs WHERE job_id=?", (encrypt_job_id,)).fetchone()
        if row:
            return row[0]
    salary = detail.get("salary", "")
    return save_job(
        job_id          = encrypt_job_id,
        company         = company,
        position        = position,
        jd              = jd,
        city            = detail.get("city", ""),
        salary          = salary,
        salary_ok       = 1 if salary else 0,
        recruiter_name  = detail.get("recruiterName",  ""),
        recruiter_title = detail.get("recruiterTitle", ""),
        source          = "chat",
    )


def get_job_by_encrypt_id(encrypt_job_id: str) -> dict | None:
    """用 encrypt_job_id 匹配 jobs 表的 job_id 字段。"""
    with _conn() as c:
        row = c.execute(
            "SELECT id, company, position, jd, salary, greeted, should_apply, city FROM jobs WHERE job_id=?",
            (encrypt_job_id,)
        ).fetchone()
        return dict(row) if row else None


def update_job_by_encrypt_id(
    encrypt_job_id: str,
    *,
    jd: str = "",
    experience: str = "",
    education: str = "",
    company_size: str = "",
    salary: str = "",
    salary_ok: int = 0,
    city: str = "",
    recruiter_name: str = "",
    recruiter_title: str = "",
    greeted: int = 0,
    score: int = -1,
    should_apply: int = -1,
    key_matches: list | None = None,
    missing_skills: list | None = None,
    skip_reason: str = "",
) -> bool:
    """更新已有岗位的分析结果和沟通状态（按 encryptJobId 精确匹配）。返回是否更新成功。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        cur = c.execute(
            """
            UPDATE jobs SET
                jd = CASE WHEN ? != '' THEN ? ELSE jd END,
                experience = ?, education = ?, company_size = ?,
                salary = ?, salary_ok = ?, city = ?,
                recruiter_name = ?, recruiter_title = ?,
                greeted = ?, score = ?, should_apply = ?,
                key_matches = ?, missing_skills = ?, skip_reason = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (
                jd, jd, experience, education, company_size,
                salary, salary_ok, city,
                recruiter_name, recruiter_title,
                greeted, score, should_apply,
                json.dumps(key_matches or [], ensure_ascii=False),
                json.dumps(missing_skills or [], ensure_ascii=False),
                skip_reason,
                now,
                encrypt_job_id,
            ),
        )
        c.commit()
        return cur.rowcount > 0



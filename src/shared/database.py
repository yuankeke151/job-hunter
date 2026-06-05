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
                city          TEXT    DEFAULT '',
                analyzed      INTEGER DEFAULT 0,  -- 0=未解析 1=本次API解析 2=跳过(他端已沟通)
                score         INTEGER DEFAULT 0,
                should_apply  INTEGER DEFAULT 0,
                key_matches   TEXT    DEFAULT '',
                missing_skills TEXT   DEFAULT '',
                skip_reason   TEXT    DEFAULT '',
                greeted       INTEGER DEFAULT 0,  -- 0=未打招呼 1=本次打招呼 2=他端已打招呼
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
            ("city",           "TEXT    DEFAULT ''"),
            ("analyzed",       "INTEGER DEFAULT 0"),
            ("score",          "INTEGER DEFAULT 0"),
            ("should_apply",   "INTEGER DEFAULT 0"),
            ("key_matches",    "TEXT    DEFAULT ''"),
            ("missing_skills", "TEXT    DEFAULT ''"),
            ("skip_reason",    "TEXT    DEFAULT ''"),
            ("greeted",        "INTEGER DEFAULT 0"),
            ("resume_file",    "TEXT    DEFAULT ''"),
        ]:
            _add_col(c, col, defn)
        c.commit()


def get_job_by_content(position: str, company: str, jd: str) -> dict | None:
    """按（职位名、公司名、JD）精确查找，三者都匹配才算同一岗位。"""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM jobs WHERE position=? AND company=? AND jd=?",
            (position.strip(), company.strip(), jd.strip()),
        ).fetchone()
        return dict(row) if row else None


def save_job(
    *,
    job_id: str,
    company: str,
    position: str,
    jd: str,
    experience: str = "",
    education: str = "",
    company_size: str = "",
    city: str = "",
    analyzed: int = 0,
    score: int = 0,
    should_apply: int = 0,
    key_matches: list | None = None,
    missing_skills: list | None = None,
    skip_reason: str = "",
    greeted: int = 0,
    resume_file: str = "",
) -> int:
    """插入一条新岗位记录，返回 rowid。"""
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO jobs
                (job_id, company, position, jd, experience, education, company_size, city,
                 analyzed, score, should_apply, key_matches, missing_skills, skip_reason,
                 greeted, resume_file)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id, company.strip(), position.strip(), jd.strip(),
                experience, education, company_size, city,
                analyzed, score, should_apply,
                json.dumps(key_matches or [], ensure_ascii=False),
                json.dumps(missing_skills or [], ensure_ascii=False),
                skip_reason, greeted, resume_file,
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
                resume_sent     INTEGER DEFAULT 0,
                tendency_score  INTEGER DEFAULT 0,
                ai_reasoning    TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT (datetime('now','localtime')),
                updated_at      TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)
        # chats 表迁移：补 initiator（旧库没有此列）
        try:
            c.execute("ALTER TABLE chats ADD COLUMN initiator TEXT DEFAULT 'me'")
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
    tendency_score: int = 0,
    ai_reasoning: str = "",
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hist_json = json.dumps(chat_history or [], ensure_ascii=False)
    with _conn() as c:
        c.execute("""
            INSERT INTO chats
                (encrypt_job_id, jobs_db_id, boss_name, company, boss_title,
                 initiator, chat_history, resume_sent, tendency_score, ai_reasoning,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(encrypt_job_id) DO UPDATE SET
                jobs_db_id     = excluded.jobs_db_id,
                boss_name      = excluded.boss_name,
                company        = excluded.company,
                boss_title     = excluded.boss_title,
                initiator      = excluded.initiator,
                chat_history   = excluded.chat_history,
                resume_sent    = MAX(resume_sent, excluded.resume_sent),
                tendency_score = CASE WHEN excluded.tendency_score > 0
                                 THEN excluded.tendency_score
                                 ELSE tendency_score END,
                ai_reasoning   = CASE WHEN excluded.ai_reasoning != ''
                                 THEN excluded.ai_reasoning
                                 ELSE ai_reasoning END,
                updated_at     = excluded.updated_at
        """, (encrypt_job_id, jobs_db_id, boss_name, company, boss_title,
              initiator, hist_json, resume_sent, tendency_score, ai_reasoning, now, now))
        c.commit()


def save_job_from_chat(chat_info: dict) -> int:
    """
    将 IM 会话里读到的少量岗位信息写入 jobs 表（source='chat'）。
    若 job_id 已存在则直接返回已有 id，不重复写入。
    返回 jobs.id（失败时返回 0）。
    """
    job_id   = chat_info.get("encryptJobId", "")
    company  = chat_info.get("companyName",  "")
    position = chat_info.get("jobName",      "") or "(IM会话，职位未知)"
    city     = chat_info.get("locationName", "")
    if not job_id or not company:
        return 0
    with _conn() as c:
        row = c.execute("SELECT id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row:
            return row[0]
        cur = c.execute(
            """INSERT OR IGNORE INTO jobs
               (job_id, company, position, jd, city, source, greeted)
               VALUES (?,?,?,?,?,?,?)""",
            (job_id, company, position, "", city, "chat", 2),
        )
        c.commit()
        return cur.lastrowid or 0


def get_job_by_encrypt_id(encrypt_job_id: str) -> dict | None:
    """用 encrypt_job_id 匹配 jobs 表的 job_id 字段。"""
    with _conn() as c:
        row = c.execute(
            "SELECT id, company, position, jd FROM jobs WHERE job_id=?",
            (encrypt_job_id,)
        ).fetchone()
        return dict(row) if row else None

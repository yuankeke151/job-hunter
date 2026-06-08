"""
job_detail_reader.py — 单卡片详情读取：点击卡片 → 面板公司校验 → 读取 JD/城市/招聘者信息
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner.page_js import (
    JS_CARD_RECT, JS_PANEL_COMPANY, JS_READ_JD, JS_READ_CITY, JS_PANEL_RECRUITER,
)
from shared.cdp_utils import evaluate, cdp_click, random_delay, small_human_scroll
from shared.logger import log


def read_job_detail(tab, idx: int, company: str) -> dict:
    """
    点击卡片并读取右侧详情面板信息。

    返回 dict，包含以下 key：
        ok              : bool   是否成功读取（False 时其余字段无意义，调用方应跳过该卡片）
        skip_reason     : str    ok=False 时的跳过原因（'card_not_found' / 'panel_mismatch'）
        panel_company   : str    面板展示的公司名（用于日志）
        jd              : str
        city            : str
        recruiter_name  : str
        recruiter_title : str
    """
    small_human_scroll(tab)
    rect_raw = evaluate(tab, JS_CARD_RECT.format(idx=idx))
    if not rect_raw:
        log.warning(f"      → [跳过] 卡片 DOM 不存在（idx={idx}）")
        return {"ok": False, "skip_reason": "card_not_found"}

    rect = json.loads(rect_raw)
    cdp_click(tab, rect["x"], rect["y"])
    random_delay(1.5, 2.5)

    # ── 方案B：校验面板公司名，防止点击错位导致 JD 错配 ──────────────────────
    panel_company = evaluate(tab, JS_PANEL_COMPANY) or ""
    if panel_company and company not in panel_company and panel_company not in company:
        log.warning(f"      → [跳过] 面板公司({panel_company!r}) ≠ 卡片公司({company!r})，点击错位")
        return {"ok": False, "skip_reason": "panel_mismatch", "panel_company": panel_company}

    jd   = evaluate(tab, JS_READ_JD)   or ""
    city = evaluate(tab, JS_READ_CITY) or ""

    recruiter_raw   = evaluate(tab, JS_PANEL_RECRUITER)
    recruiter       = json.loads(recruiter_raw) if recruiter_raw else {}
    recruiter_name  = recruiter.get("recruiterName", "")
    recruiter_title = recruiter.get("recruiterTitle", "")

    return {
        "ok": True,
        "panel_company": panel_company,
        "jd": jd,
        "city": city,
        "recruiter_name": recruiter_name,
        "recruiter_title": recruiter_title,
    }

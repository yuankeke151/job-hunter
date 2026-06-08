import sys, json, time
from pathlib import Path
import pychrome
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL
from shared.cdp_utils import evaluate, cdp_click
from shared.logger import log

# ── 「查看职位」详情页抓取 ────────────────────────────────────────────────────

_JS_FIND_VIEW_JOB_BTN = """
(function() {
    const spans = Array.from(document.querySelectorAll('.position-content .right-content span'));
    const el = spans.find(s => (s.innerText || '').includes('查看职位'));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return JSON.stringify({
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        visible: r.width > 0 && r.height > 0,
    });
})()
"""

_JS_EXTRACT_JOB_DETAIL = """
(function() {
    const txt = (s) => { const e = document.querySelector(s); return e ? (e.innerText || '').trim() : ''; };

    const jobName = txt('.job-name') || txt('.job-banner .name h1');
    const salary  = txt('.job-banner .name .salary') || txt('.salary');
    const city    = txt('.job-banner .text-desc.text-city') || txt('.text-city');
    const jd      = txt('.job-sec-text');

    let companyName = '', recruiterName = '', recruiterTitle = '';
    const boss = document.querySelector('.job-boss-info');
    if (boss) {
        const nameEl = boss.querySelector('h2.name');
        if (nameEl) {
            for (const node of nameEl.childNodes) {
                if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
                    recruiterName = node.textContent.trim();
                    break;
                }
            }
        }
        const attrText = (boss.querySelector('.boss-info-attr')?.innerText || '').trim();
        const parts = attrText.split('\\u00b7').map(s => s.trim()).filter(Boolean);
        if (parts.length >= 2) {
            companyName    = parts[0];
            recruiterTitle = parts[1];
        } else if (parts.length === 1) {
            companyName = parts[0];
        }
    }

    return JSON.stringify({jobName, city, salary, companyName, jd, recruiterName, recruiterTitle});
})()
"""


def fetch_job_detail_via_view_job(tab) -> dict | None:
    """
    点击聊天页右侧职位面板的「查看职位」，在新打开的 job_detail 标签页中
    提取完整岗位信息（岗位名称/地点/薪资/公司名称/JD/招聘者姓名/招聘者title），
    随后关闭该标签页并切回聊天页。

    该页面薪资为明文（与扫描页 .job-salary 的 kanzhun-mix 混淆不同），无需解码。
    任何步骤失败均返回 None，并尽量保证标签页已清理、焦点已切回聊天页。
    """
    raw = evaluate(tab, _JS_FIND_VIEW_JOB_BTN)
    if not raw or raw == "null":
        log.info("  [查看职位] 未找到「查看职位」按钮，跳过")
        return None
    btn = json.loads(raw)
    if not btn["visible"]:
        log.info("  [查看职位] 按钮不可见，跳过")
        return None

    browser = pychrome.Browser(url=CDP_CHAT_URL)
    before_ids = {t["id"] for t in requests.get(f"{CDP_CHAT_URL}/json", timeout=5).json()}

    cdp_click(tab, btn["x"], btn["y"])
    log.info("  [查看职位] 已点击，等待详情页打开...")

    detail_meta = None
    deadline = time.time() + 8.0
    while time.time() < deadline:
        for t in requests.get(f"{CDP_CHAT_URL}/json", timeout=5).json():
            if (t["id"] not in before_ids
                    and t.get("type") == "page"
                    and "/job_detail/" in t.get("url", "")):
                detail_meta = t
                break
        if detail_meta:
            break
        time.sleep(0.5)

    if not detail_meta:
        log.info("  [查看职位] 超时未检测到详情页，跳过")
        return None

    detail_tab = next((t for t in browser.list_tab() if t.id == detail_meta["id"]), None)
    if detail_tab is None:
        log.info("  [查看职位] 未能连接详情页标签，跳过")
        return None

    detail_tab.start()
    try:
        for _ in range(10):
            if evaluate(detail_tab, "!!document.querySelector('.job-name')"):
                break
            time.sleep(0.5)
        raw_detail = evaluate(detail_tab, _JS_EXTRACT_JOB_DETAIL)
    finally:
        detail_tab.stop()
        try:
            browser.close_tab(detail_meta["id"])
        except Exception as e:
            log.warning(f"  [查看职位] 关闭详情页标签失败: {e}")
        try:
            browser.activate_tab(tab)
        except Exception as e:
            log.warning(f"  [查看职位] 切回聊天页标签失败: {e}")

    if not raw_detail:
        log.info("  [查看职位] 详情页提取失败")
        return None

    detail = json.loads(raw_detail)
    log.info(f"  [查看职位] 已获取详情：{detail.get('jobName','')} "
             f"@ {detail.get('companyName','')}（JD {len(detail.get('jd',''))} 字）")
    return detail

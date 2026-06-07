"""
debug_view_job.py — 聊天页「查看职位」功能测试

流程：
  1. 在聊天页（/web/geek/chat）找到右侧职位信息面板的「查看职位」按钮并点击
  2. 检测新打开的 job_detail 标签页（diff 标签列表）
  3. 连接新标签页，提取：岗位名称 / 地点 / 薪资 / 公司名称 / 职位描述 / 招聘者姓名 / 招聘者 title
  4. 关闭该标签页，焦点返回聊天页

发现记录：
  - job_detail 页面的薪资（.salary）是明文（非 kanzhun-mix 混淆），与扫描页 .job-salary 不同，无需解码

运行前提：
  - start_chrome_chat.bat 已启动（port 9223）
  - 已登录并打开任意一个有 JD 的会话（右侧职位信息面板可见「查看职位」按钮）

用法：
  python src/debug/debug_view_job.py
"""
import sys
import time
import json
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL
from shared.cdp_utils import cdp_click, evaluate

CDP_URL = CDP_CHAT_URL

OK   = "[OK  ]"
MISS = "[MISS]"
INFO = "[INFO]"
WARN = "[WARN]"


def sep(title="", width=64):
    print()
    print("=" * width)
    if title:
        print(f"  {title}")
        print("=" * width)


def list_tabs() -> list[dict]:
    return requests.get(f"{CDP_URL}/json", timeout=5).json()


def find_chat_tab() -> dict | None:
    for t in list_tabs():
        if t.get("type") == "page" and "/web/geek/chat" in t.get("url", ""):
            return t
    return None


# ── 第一步：定位并点击「查看职位」 ─────────────────────────────────────────

_JS_FIND_VIEW_JOB_BTN = """
(function() {
    const spans = Array.from(document.querySelectorAll('.position-content .right-content span'));
    const el = spans.find(s => (s.innerText || '').includes('查看职位'));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return JSON.stringify({
        text: el.innerText.trim(),
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        visible: r.width > 0 && r.height > 0,
    });
})()
"""


def click_view_job(tab) -> bool:
    sep("STEP 1 — 定位并点击「查看职位」")
    raw = evaluate(tab, _JS_FIND_VIEW_JOB_BTN)
    if not raw or raw == "null":
        print(f"  {MISS} 未找到「查看职位」按钮（当前会话是否含 JD？）")
        return False
    info = json.loads(raw)
    if not info["visible"]:
        print(f"  {WARN} 按钮不可见: {info}")
        return False
    print(f"  {OK} 找到按钮: text={info['text']!r} pos=({info['x']:.0f}, {info['y']:.0f})")
    cdp_click(tab, info["x"], info["y"])
    print(f"  {INFO} 已点击，等待新标签页打开...")
    return True


# ── 第二步：检测新打开的 job_detail 标签页 ──────────────────────────────────

def wait_for_job_detail_tab(before_ids: set[str], timeout: float = 8.0) -> dict | None:
    sep("STEP 2 — 检测新标签页")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t in list_tabs():
            if (t["id"] not in before_ids
                    and t.get("type") == "page"
                    and "/job_detail/" in t.get("url", "")):
                print(f"  {OK} 发现新标签页: id={t['id']}")
                print(f"       url={t['url']}")
                return t
        time.sleep(0.5)
    print(f"  {MISS} 超时未检测到新的 job_detail 标签页")
    return None


# ── 第三步：提取职位详情 ───────────────────────────────────────────────────

_JS_EXTRACT_DETAIL = """
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


def extract_job_detail(tab) -> dict | None:
    sep("STEP 3 — 提取职位详情")
    # 等待页面关键元素渲染
    for _ in range(10):
        if evaluate(tab, "!!document.querySelector('.job-name')"):
            break
        time.sleep(0.5)

    raw = evaluate(tab, _JS_EXTRACT_DETAIL)
    if not raw:
        print(f"  {MISS} 提取失败，JS 无返回")
        return None
    info = json.loads(raw)

    fields = [
        ("岗位名称", "jobName"),
        ("地点",     "city"),
        ("薪资",     "salary"),
        ("公司名称", "companyName"),
        ("招聘者姓名", "recruiterName"),
        ("招聘者title", "recruiterTitle"),
    ]
    for label, key in fields:
        val = info.get(key, "")
        marker = OK if val else MISS
        print(f"  {marker} {label}: {val!r}")

    jd = info.get("jd", "")
    marker = OK if jd else MISS
    print(f"  {marker} 职位描述（前 120 字）: {jd[:120]!r}")
    print(f"  {INFO} 职位描述总长度: {len(jd)} 字")
    return info


# ── 第四步：关闭新标签页，焦点返回聊天页 ────────────────────────────────────

def close_and_return(browser, detail_tab_id: str, chat_tab_id: str):
    sep("STEP 4 — 关闭新标签页并返回聊天页")
    browser.close_tab(detail_tab_id)
    print(f"  {OK} 已关闭 job_detail 标签页 ({detail_tab_id})")
    time.sleep(0.5)
    browser.activate_tab(chat_tab_id)
    print(f"  {OK} 已激活聊天页标签 ({chat_tab_id})")

    remaining = [t["id"] for t in list_tabs()]
    if detail_tab_id not in remaining:
        print(f"  {OK} 确认 job_detail 标签页已从标签列表移除")
    else:
        print(f"  {WARN} job_detail 标签页仍在列表中: {detail_tab_id}")


def main():
    sep("聊天页「查看职位」功能测试")

    chat_meta = find_chat_tab()
    if not chat_meta:
        print(f"  {MISS} 未找到聊天页标签（请确认已打开 /web/geek/chat 并登录）")
        return
    print(f"  {OK} 聊天页标签: id={chat_meta['id']}")

    browser = pychrome.Browser(url=CDP_URL)
    chat_tab = next(t for t in browser.list_tab() if t.id == chat_meta["id"])
    chat_tab.start()

    try:
        before_ids = {t["id"] for t in list_tabs()}

        if not click_view_job(chat_tab):
            return

        detail_meta = wait_for_job_detail_tab(before_ids)
        if not detail_meta:
            return

        detail_tab = next(t for t in browser.list_tab() if t.id == detail_meta["id"])
        detail_tab.start()
        try:
            extract_job_detail(detail_tab)
        finally:
            detail_tab.stop()

        close_and_return(browser, detail_meta["id"], chat_meta["id"])

    finally:
        chat_tab.stop()

    sep("测试结束")


if __name__ == "__main__":
    main()

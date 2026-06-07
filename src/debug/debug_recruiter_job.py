"""
debug_recruiter_job.py — 岗位列表页「招聘者姓名 / title」选择器探测

背景：
  聊天页「查看职位」详情页已能提取 recruiterName / recruiterTitle
  （.job-boss-info 内 h2.name 首个文本节点 + .boss-info-attr 按「·」拆分），
  但 scanner 列表页卡片 / JD 详情面板尚无对应字段，本脚本探测两处是否存在
  类似结构，找出可用选择器。

运行前提：
  - start_chrome_job.bat 已启动（port 9222）
  - 已登录并打开职位搜索列表页，建议先点击一张卡片打开右侧 JD 详情面板

用法：
  python src/debug/debug_recruiter_job.py
"""
import sys
import json
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_SCANNER_URL

# Windows 控制台默认 GBK，岗位文本中可能含私用区字符（kanzhun-mix 薪资混淆码点等），
# 直接 print 会抛 UnicodeEncodeError，改为 UTF-8 输出并替换无法编码的字符
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CDP_URL = CDP_SCANNER_URL

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


def connect():
    try:
        tabs_info = requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        print(f"[失败] 无法连接 {CDP_URL}: {e}"); sys.exit(1)

    boss = next(
        (t for t in tabs_info
         if "zhipin.com" in t.get("url", "") and t.get("type") == "page"), None
    )
    if not boss:
        print("[失败] 未找到 BOSS直聘 job 列表标签页"); sys.exit(1)

    print(f"[标签页] {boss.get('title','')[:60]}")
    print(f"[URL]    {boss.get('url','')}")

    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == boss["id"]), None)
    if not tab:
        print("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    print("[CDP] 连接成功")
    return tab


def eval_js(tab, js: str, label: str = ""):
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=10)
        return raw.get("result", {}).get("value")
    except Exception as e:
        print(f"  {WARN} {label} JS错误: {e}")
        return None


def _print_items(items):
    print(f"  {'tag':<6}  {'class':<55}  text")
    print(f"  {'─'*6}  {'─'*55}  {'─'*30}")
    for it in items:
        print(f"  {it['tag']:<6}  {it['cls'][:55]:<55}  {it['txt'][:30]}")


# ── GROUP 1：卡片 .boss-info 内部结构 ─────────────────────────────────────────

def check_card_boss_info(tab):
    sep("GROUP 1 — 卡片 .boss-info 内部结构（寻找招聘者姓名/title）")
    js = """
    (function() {
        const card = document.querySelector('.job-card-wrap');
        if (!card) return JSON.stringify({ ok: false, reason: '未找到 .job-card-wrap' });
        const boss = card.querySelector('.boss-info');
        if (!boss) return JSON.stringify({ ok: false, reason: '未找到 .boss-info' });
        const items = Array.from(boss.querySelectorAll('*'))
            .filter(el => el.className && typeof el.className === 'string' && el.className.trim())
            .map(el => ({
                tag: el.tagName.toLowerCase(),
                cls: el.className.replace(/\\s+/g,' ').trim().slice(0,55),
                txt: (el.innerText||'').replace(/\\s+/g,' ').trim().slice(0,40),
            }));
        return JSON.stringify({ ok: true, html: boss.innerHTML.slice(0, 600), items });
    })()
    """
    raw = eval_js(tab, js, "卡片 .boss-info 结构")
    if not raw:
        print(f"  {MISS} JS 无返回")
        return
    d = json.loads(raw)
    if not d["ok"]:
        print(f"  {MISS} {d['reason']}")
        return

    print(f"  {INFO} .boss-info 内部元素（共 {len(d['items'])} 个，含 class）：")
    _print_items(d["items"])
    print(f"\n  {INFO} innerHTML 预览（前 600 字）:")
    print(f"  {d['html']}")


# ── GROUP 2：JD 详情面板招聘者信息 ────────────────────────────────────────────

_JS_EXTRACT_RECRUITER = """
(function() {
    const boss = document.querySelector('.job-boss-info');
    if (!boss) return JSON.stringify({ ok: false, reason: '未找到 .job-boss-info' });

    let recruiterName = '', recruiterTitle = '', companyName = '';
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

    return JSON.stringify({
        ok: true,
        recruiterName, recruiterTitle, companyName,
        attrText,
        html: boss.innerHTML.slice(0, 1200),
    });
})()
"""


def check_jd_panel_recruiter(tab):
    sep("GROUP 2 — JD 详情面板招聘者信息（需先点击一张卡片）")
    js = """
    (function() {
        const header = document.querySelector('.job-detail-header');
        if (!header) return JSON.stringify({ ok: false, reason: '未找到 .job-detail-header（请先点击卡片）' });

        const items = Array.from(header.querySelectorAll('*'))
            .filter(el => el.className && typeof el.className === 'string' && el.className.trim())
            .map(el => ({
                tag: el.tagName.toLowerCase(),
                cls: el.className.replace(/\\s+/g,' ').trim().slice(0,55),
                txt: (el.innerText||'').replace(/\\s+/g,' ').trim().slice(0,40),
            }));
        return JSON.stringify({ ok: true, items: items.slice(0, 40) });
    })()
    """
    raw = eval_js(tab, js, "JD面板结构")
    if not raw:
        print(f"  {MISS} JS 无返回")
    else:
        d = json.loads(raw)
        if not d["ok"]:
            print(f"  {MISS} {d['reason']}")
        else:
            print(f"  {INFO} .job-detail-header 内部元素（共 {len(d['items'])} 个，含 class）：")
            _print_items(d["items"])

    # 直接用 chat/session_actions._JS_EXTRACT_JOB_DETAIL 同款逻辑
    # （h2.name 首文本节点 + .boss-info-attr 按「·」拆分）在 .job-boss-info 上测试
    subsep_title = "套用聊天页同款提取逻辑（.job-boss-info → h2.name / .boss-info-attr）"
    print(f"\n  {'─'*4} {subsep_title} {'─'*max(0, 64 - len(subsep_title) - 8)}")
    raw2 = eval_js(tab, _JS_EXTRACT_RECRUITER, "招聘者信息提取")
    if not raw2:
        print(f"  {MISS} JS 无返回")
        return
    d2 = json.loads(raw2)
    if not d2["ok"]:
        print(f"  {MISS} {d2['reason']}")
        return

    for label, key in [("招聘者姓名", "recruiterName"),
                       ("招聘者title", "recruiterTitle"),
                       ("公司名称（副产物）", "companyName")]:
        val = d2.get(key, "")
        marker = OK if val else MISS
        print(f"  {marker} {label}: {val!r}")
    print(f"  {INFO} .boss-info-attr 原文: {d2.get('attrText','')!r}")
    print(f"\n  {INFO} .job-boss-info innerHTML 预览（前 1200 字）:")
    print(f"  {d2['html']}")


def main():
    sep("debug_recruiter_job.py — 列表页招聘者姓名/title 选择器探测")
    tab = connect()
    try:
        check_card_boss_info(tab)
        check_jd_panel_recruiter(tab)
    finally:
        try:
            tab.stop()
        except Exception:
            pass
    sep("探测结束")


if __name__ == "__main__":
    main()

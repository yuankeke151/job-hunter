"""
debug_salary_chat.py — 聊天界面薪资元素提取测试

与 debug_salary.py（扫描页 .job-salary，kanzhun-mix 字体混淆）不同，
本脚本测试聊天界面右侧职位信息面板中的薪资展示是否同样被混淆。

测试来源（两条独立路径，互相验证）：
  A. DOM 元素：.position-content .salary（渲染文字）
  B. JS 数据对象：window.chat.communicating.salaryDesc / lowSalary / highSalary

运行前提：
  - start_chrome_chat.bat 已启动（port 9223）
  - 已登录并打开任意一个聊天会话（右侧出现职位信息面板）

用法：
  python src/debug/debug_salary_chat.py
"""
import sys
import json
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL

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


def eval_js(tab, js: str, label: str = ""):
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=15)
        return raw.get("result", {}).get("value")
    except Exception as e:
        print(f"  {WARN} {label} JS错误: {e}")
        return None


# ── GROUP 1：DOM 元素 .salary 提取（渲染文字 + 码点）──────────────────────────

def check_dom_salary(tab):
    sep("GROUP 1 — DOM 元素提取（.position-content .salary）")

    js = """
    (function() {
        const el = document.querySelector('.position-content .salary')
                || document.querySelector('.salary');
        if (!el) return null;
        const txt = el.innerText || el.textContent || '';
        const codes = Array.from(txt).map(c => c.codePointAt(0));
        return JSON.stringify({
            raw: txt,
            codes,
            outerHTML: el.outerHTML.slice(0, 200),
            fontFamily: window.getComputedStyle(el).fontFamily,
            parentClass: el.parentElement ? el.parentElement.className : '',
        });
    })()
    """
    raw = eval_js(tab, js, ".salary 元素")
    if not raw or raw == "null":
        print(f"  {MISS} 未找到 .salary 元素（当前是否已打开某个会话？）")
        return None

    info = json.loads(raw)
    codes = info["codes"]
    private = [c for c in codes if 0xE000 <= c <= 0xF8FF]

    display_codes = " ".join(
        f"U+{c:04X}{'*' if 0xE000 <= c <= 0xF8FF else ''}" for c in codes
    )
    print(f"  {OK} 找到 .salary 元素")
    print(f"    outerHTML   : {info['outerHTML']!r}")
    print(f"    parent.class: {info['parentClass']!r}")
    print(f"    raw text    : {info['raw']!r}")
    print(f"    codepoints  : {display_codes}")
    print(f"    font-family : {info['fontFamily']!r}")

    if private:
        print(f"\n  {WARN} 发现 {len(private)} 个私用区码点（* 标记）— 仍被 kanzhun-mix 混淆")
    else:
        print(f"\n  {OK} 未发现私用区码点 — 文本未被混淆，可直接读取！")

    return info


# ── GROUP 2：JS 数据对象 window.chat.communicating ───────────────────────────

def check_communicating_salary(tab):
    sep("GROUP 2 — JS 数据对象（window.chat.communicating）")

    js = """
    (function() {
        const c = window.chat && window.chat.communicating;
        if (!c) return null;
        const desc = c.salaryDesc || '';
        const codes = Array.from(desc).map(ch => ch.codePointAt(0));
        return JSON.stringify({
            salaryDesc: desc,
            codes,
            lowSalary: c.lowSalary,
            highSalary: c.highSalary,
            jobName: c.jobName,
            brandName: c.brandName,
        });
    })()
    """
    raw = eval_js(tab, js, "window.chat.communicating")
    if not raw or raw == "null":
        print(f"  {MISS} window.chat.communicating 不存在（是否已打开会话？）")
        return None

    info = json.loads(raw)
    codes = info["codes"]
    private = [c for c in codes if 0xE000 <= c <= 0xF8FF]

    display_codes = " ".join(
        f"U+{c:04X}{'*' if 0xE000 <= c <= 0xF8FF else ''}" for c in codes
    )
    print(f"  {OK} window.chat.communicating 存在")
    print(f"    jobName     : {info['jobName']!r}")
    print(f"    brandName   : {info['brandName']!r}")
    print(f"    salaryDesc  : {info['salaryDesc']!r}")
    print(f"    codepoints  : {display_codes}")
    print(f"    lowSalary   : {info['lowSalary']!r}  (类型: {type(info['lowSalary']).__name__})")
    print(f"    highSalary  : {info['highSalary']!r}  (类型: {type(info['highSalary']).__name__})")

    if private:
        print(f"\n  {WARN} salaryDesc 含 {len(private)} 个私用区码点 — 仍被混淆")
    else:
        print(f"\n  {OK} salaryDesc 为明文，且 lowSalary/highSalary 为原始数值 — 无需解码！")

    return info


# ── GROUP 3：交叉验证 + 结论 ─────────────────────────────────────────────────

def cross_check(dom_info, comm_info):
    sep("GROUP 3 — 交叉验证与结论")

    if not dom_info and not comm_info:
        print(f"  {MISS} 两条路径均未取到数据，请确认已打开一个会话窗口")
        return

    dom_clean  = dom_info  is not None and not any(0xE000 <= c <= 0xF8FF for c in dom_info["codes"])
    comm_clean = comm_info is not None and not any(0xE000 <= c <= 0xF8FF for c in comm_info["codes"])

    print(f"  {'来源':<32}  {'是否混淆':<10}  示例值")
    print(f"  {'─'*32}  {'─'*10}  {'─'*30}")
    if dom_info:
        print(f"  {'DOM .position-content .salary':<32}  "
              f"{'是' if not dom_clean else '否':<10}  {dom_info['raw']!r}")
    if comm_info:
        print(f"  {'window.chat.communicating.salaryDesc':<32}  "
              f"{'是' if not comm_clean else '否':<10}  {comm_info['salaryDesc']!r}")
        print(f"  {'window.chat.communicating.lowSalary/high':<32}  "
              f"{'否（原始数值）':<10}  {comm_info['lowSalary']}-{comm_info['highSalary']}")

    print()
    if comm_clean:
        print(f"  {OK} 结论：聊天界面薪资【未被 kanzhun-mix 混淆】")
        print(f"  {INFO} 推荐提取方式（优先级从高到低）：")
        print(f"       1. window.chat.communicating.lowSalary / highSalary")
        print(f"          → 原始数值（int），最稳定，无需任何解析")
        print(f"       2. window.chat.communicating.salaryDesc")
        print(f"          → 明文字符串（如 '25-50K·14薪'），可直接入库")
        print(f"       3. DOM .position-content .salary（仅作为兜底/校验）")
        print(f"\n  {INFO} 与扫描页 .job-salary 的混淆机制【不同】：")
        print(f"       扫描页用私用区 Unicode + kanzhun-mix 字体渲染数字，读取得到乱码；")
        print(f"       聊天页该字段直接来自接口返回的 JSON 数据，未经字体混淆处理。")
    else:
        print(f"  {WARN} 结论：聊天界面薪资仍被混淆，需要复用 debug_salary.py 中的解码方案")


# ── 连接 CDP ──────────────────────────────────────────────────────────────────

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
        print("[失败] 未找到 BOSS直聘 标签页"); sys.exit(1)

    print(f"[标签页] {boss.get('title','')[:60]}")
    print(f"[URL]    {boss.get('url','')}")

    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == boss["id"]), None)
    if not tab:
        print("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    print("[CDP] 连接成功")
    return tab


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  debug_salary_chat.py — 聊天界面薪资提取测试")
    print("=" * 64)

    tab = connect()
    try:
        dom_info  = check_dom_salary(tab)
        comm_info = check_communicating_salary(tab)
        cross_check(dom_info, comm_info)
    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()

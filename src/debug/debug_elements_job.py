"""
debug_elements_job.py — 检查 scanner 使用的所有页面元素

运行前提：
  - start_chrome_job.bat 已启动（port 9222）
  - 已手动登录并导航到职位搜索列表页（有岗位卡片）
  - 建议先点击一张卡片，让右侧 JD 面板打开，再运行脚本

用法：
  python debug_elements_job.py

输出：
  每个选择器的状态（OK / MISS）、数量、样本文字，以及结构信息。
  末尾汇总所有缺失选择器及其在代码中的位置，可直接提供给 Claude Code 修正程序。
"""
import sys
import json
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_SCANNER_URL

CDP_URL = CDP_SCANNER_URL

# ── 颜色/标记 ─────────────────────────────────────────────────────────────────
OK   = "[OK  ]"
MISS = "[MISS]"
WARN = "[WARN]"
INFO = "[INFO]"

missing_report: list[tuple[str, str]] = []   # (selector, code_location)


def sep(title="", width=64):
    print()
    print("=" * width)
    if title:
        print(f"  {title}")
        print("=" * width)


def subsep(title="", width=64):
    print(f"\n  {'─'*4} {title} {'─'*(width - len(title) - 8)}")


def check(tab, sel: str, desc: str, code_loc: str,
          sample_attr: str = "innerText", limit: int = 40) -> int:
    """
    检查 sel 在页面中是否存在，打印结果，返回匹配数量。
    """
    js = f"""
    (function() {{
        const els = Array.from(document.querySelectorAll({json.dumps(sel)}));
        if (!els.length) return JSON.stringify({{ count: 0, samples: [] }});
        const samples = els.slice(0, 3).map(el => {{
            const raw = (el.innerText || el.getAttribute('href') || el.className || '').trim();
            return raw.replace(/\\s+/g, ' ').slice(0, {limit});
        }});
        return JSON.stringify({{ count: els.length, samples }});
    }})()
    """
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=10)
        val = raw.get("result", {}).get("value")
        if not val:
            raise ValueError("no value")
        data = json.loads(val)
    except Exception as e:
        print(f"  {MISS} {sel:<48} JS错误: {e}")
        missing_report.append((sel, code_loc))
        return 0

    count = data["count"]
    samples = data["samples"]
    sample_str = " | ".join(f'"{s}"' for s in samples if s) or "(无文字)"

    if count > 0:
        print(f"  {OK} {sel:<48} count={count:<4} {desc}")
        if samples:
            print(f"         样本: {sample_str}")
    else:
        print(f"  {MISS} {sel:<48} count=0    {desc}  ← 需更新！")
        print(f"         代码位置: {code_loc}")
        missing_report.append((sel, code_loc))

    return count


def eval_js(tab, js: str, label: str = ""):
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=10)
        return raw.get("result", {}).get("value")
    except Exception as e:
        print(f"  {WARN} {label} JS错误: {e}")
        return None


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


# ── 检查函数 ──────────────────────────────────────────────────────────────────

def check_card_list(tab):
    sep("GROUP 1 — 岗位卡片列表")

    check(tab, ".job-card-wrap",
          "卡片根元素（_JS_EXTRACT_CARDS 入口）",
          "scanner.py: _JS_EXTRACT_CARDS querySelectorAll('.job-card-wrap')")

    check(tab, ".job-name",
          "职位名称",
          "scanner.py: _JS_EXTRACT_CARDS q('.job-name')")

    check(tab, ".boss-info .boss-name",
          "公司名称（.boss-info 内的 .boss-name）",
          "scanner.py: _JS_EXTRACT_CARDS q('.boss-info .boss-name')")

    check(tab, ".boss-name",
          "公司名备选（无 .boss-info 前缀）",
          "scanner.py: _JS_EXTRACT_CARDS q('.boss-name') 备选")

    check(tab, ".job-salary",
          "薪资（kanzhun-mix 字体混淆，预期乱码）",
          "CLAUDE.md 说明: 暂不解析")

    check(tab, ".tag-list li",
          "卡片标签（经验/学历，取 [0]=经验 [1]=学历）",
          "scanner.py: _JS_EXTRACT_CARDS jobTags")

    check(tab, ".company-tag-list li",
          "公司规模标签（主选择器）",
          "scanner.py: _JS_EXTRACT_CARDS compTags 主选")

    check(tab, "[class*='company-tag'] li",
          "公司规模标签（备选1，class 含 company-tag 的 li）",
          "scanner.py: _JS_EXTRACT_CARDS compTags 备选")

    check(tab, "[class*='company-tag'] span",
          "公司规模标签（备选2，class 含 company-tag 的 span）",
          "scanner.py: _JS_EXTRACT_CARDS compTags 备选")

    # 岗位 URL / job_id
    subsep("岗位链接 & job_id 提取")
    js_link = """
    (function() {
        const links = Array.from(document.querySelectorAll("a[href*='/job_detail/']"));
        if (!links.length) return JSON.stringify({ count: 0, sample: '' });
        const href = links[0].getAttribute('href') || '';
        const m    = href.match(/\/job_detail\/([^.?/]+)/);
        return JSON.stringify({ count: links.length, sample: href.slice(0,80),
                                job_id: m ? m[1] : '(未提取到)' });
    })()
    """
    raw = eval_js(tab, js_link, "job_id 提取")
    if raw:
        d = json.loads(raw)
        status = OK if d["count"] > 0 else MISS
        print(f"  {status} a[href*='/job_detail/']           count={d['count']:<4} job_id 链接")
        if d["count"]:
            print(f"         href样本: {d['sample']!r}")
            print(f"         job_id:  {d['job_id']!r}")
        else:
            missing_report.append(("a[href*='/job_detail/']",
                                   "scanner.py: _JS_EXTRACT_CARDS href match"))


def check_jd_panel(tab):
    sep("GROUP 2 — JD 详情面板（需先点击一张卡片）")

    n = check(tab, ".job-detail-body",
              "JD 内容容器（_JS_READ_JD 入口）",
              "scanner.py: _JS_READ_JD querySelector('.job-detail-body')")
    if n == 0:
        print(f"  {WARN} JD 面板不可见，以下检查结果可能为 MISS，请先点击一张卡片再重跑")

    check(tab, ".job-detail-body h3.title",
          "JD 锚点标题（遍历从此元素之后开始）",
          "scanner.py: _JS_READ_JD body.querySelector('h3.title')")

    check(tab, ".job-detail-header .tag-list li",
          "JD header 标签列（城市/经验/学历）",
          "scanner.py: _JS_READ_CITY（及 CLAUDE.md 选择器表）")

    check(tab, ".job-detail-header .tag-list li:first-child a",
          "城市（标签列第一项的 <a>）",
          "scanner.py: _JS_READ_CITY 主选择器")

    check(tab, ".job-detail-header .tag-list li:first-child",
          "城市备选（标签列第一项，无 <a> 时）",
          "scanner.py: _JS_READ_CITY 备选")

    check(tab, ".job-detail-header .company-info .name",
          "JD 面板公司名（防错配校验，主选）",
          "scanner.py: _JS_PANEL_COMPANY 主选")

    check(tab, ".job-detail-header .name",
          "JD 面板公司名（备选1）",
          "scanner.py: _JS_PANEL_COMPANY 备选1")

    check(tab, ".company-info .name",
          "JD 面板公司名（备选2）",
          "scanner.py: _JS_PANEL_COMPANY 备选2")

    # 全文提取测试
    subsep("JD 全文提取测试（_JS_READ_JD）")
    _JS_READ_JD = """
    (function() {
        const body = document.querySelector('.job-detail-body');
        if (!body) return JSON.stringify({ ok: false, reason: '未找到 .job-detail-body' });
        const h3 = body.querySelector('h3.title');
        if (!h3) {
            const txt = body.innerText.trim().slice(0, 150);
            return JSON.stringify({ ok: true, mode: '兜底全文', preview: txt,
                                    len: body.innerText.trim().length });
        }
        const TAIL_CLS  = ['boss-info', 'detail-op', 'work-addr', 'job-link', 'job-tools', 'hot-link'];
        const TAIL_TEXT = ['去App', '与BOSS随时沟通', '工作地址', '查看更多信息'];
        const parts = [];
        let el = h3.nextElementSibling;
        while (el) {
            const cls  = (el.className || '').toString();
            const text = (el.innerText  || '').trim();
            if (TAIL_CLS.some(c => cls.includes(c))) break;
            if (TAIL_TEXT.some(t => text.includes(t))) break;
            if (text) parts.push(text);
            el = el.nextElementSibling;
        }
        const full = parts.join('\\n');
        return JSON.stringify({ ok: full.length > 0, mode: 'h3锚点', preview: full.slice(0, 150),
                                len: full.length });
    })()
    """
    raw = eval_js(tab, _JS_READ_JD, "JD全文提取")
    if raw:
        d = json.loads(raw)
        if d["ok"]:
            print(f"  {OK} JD 提取成功  mode={d['mode']}  总长={d['len']}字")
            print(f"         预览: {d['preview'][:120]!r}...")
        else:
            print(f"  {MISS} JD 提取失败: {d.get('reason','未知')}")
            missing_report.append(("_JS_READ_JD 整体逻辑",
                                   "scanner.py: _JS_READ_JD（可能 .job-detail-body 或 h3.title 结构变化）"))


def check_action_buttons(tab):
    sep("GROUP 3 — 操作按钮")

    check(tab, ".op-btn-chat",
          "「立即沟通」按钮（_JS_CHAT_BTN_RECT）",
          "scanner.py: _JS_CHAT_BTN_RECT querySelector('.op-btn-chat')")

    check(tab, ".cancel-btn",
          "弹窗「留在此页」按钮（_JS_STAY_BTN_RECT）",
          "scanner.py: _JS_STAY_BTN_RECT querySelector('.cancel-btn')")

    check(tab, ".expect-item",
          "求职期望 tab（回退后恢复用）",
          "scanner.py: _JS_EXPECT_TAB_RECT querySelectorAll('.expect-item')")

    # 检查是否含目标职位文字
    js_expect = """
    (function() {
        const items = Array.from(document.querySelectorAll('.expect-item'));
        return JSON.stringify(items.map(el => (el.innerText||'').trim().slice(0,40)));
    })()
    """
    raw = eval_js(tab, js_expect, "expect-item 文字")
    if raw:
        texts = json.loads(raw)
        target = [t for t in texts if "数据分析师" in t]
        if target:
            print(f"  {OK} .expect-item 含「数据分析师」: {target}")
        elif texts:
            print(f"  {WARN} .expect-item 存在但无「数据分析师」，当前内容: {texts}")
        # 若 .expect-item 不存在已在上方 check() 里报告


def check_card_extraction(tab):
    sep("GROUP 4 — 卡片字段提取（_JS_EXTRACT_CARDS 完整运行）")

    _JS_EXTRACT_CARDS = """
    (function() {
        const cards = document.querySelectorAll('.job-card-wrap');
        if (!cards.length) return JSON.stringify({ count: 0, cards: [] });
        const result = Array.from(cards).slice(0, 3).map((card, idx) => {
            const q = (sel) => { const e = card.querySelector(sel); return e ? e.innerText.trim() : ''; };
            const jobTags = Array.from(card.querySelectorAll('.tag-list li'))
                                .map(e => e.innerText.trim());
            const compTags = Array.from(card.querySelectorAll(
                '.company-tag-list li, [class*="company-tag"] li, [class*="company-tag"] span'
            )).map(e => e.innerText.trim());
            const link  = card.querySelector("a[href*='/job_detail/']");
            const href  = link ? link.getAttribute('href') : '';
            const match = href.match(/\\/job_detail\\/([^.?/]+)/);
            return {
                idx,
                name        : q('.job-name')         || q('[class*="job-name"]'),
                company     : q('.boss-info .boss-name') || q('.boss-name'),
                experience  : jobTags[0] || '',
                education   : jobTags[1] || '',
                company_size: compTags.find(t => t.includes('人')) || compTags[1] || compTags[0] || '',
                job_id      : match ? match[1] : '',
            };
        });
        return JSON.stringify({ count: cards.length, cards: result });
    })()
    """
    raw = eval_js(tab, _JS_EXTRACT_CARDS, "_JS_EXTRACT_CARDS")
    if not raw:
        print(f"  {MISS} _JS_EXTRACT_CARDS 执行失败（无返回值）")
        return

    d = json.loads(raw)
    print(f"  {INFO} 共找到 {d['count']} 张卡片，显示前 {len(d['cards'])} 张提取结果：")
    fields = ["name", "company", "experience", "education", "company_size", "job_id"]
    for card in d["cards"]:
        print(f"\n  卡片 [{card['idx']}]:")
        for f in fields:
            val = card.get(f, "")
            status = OK if val else WARN
            print(f"    {status} {f:<14} = {val!r}")


def check_structural_info(tab):
    sep("GROUP 5 — 辅助结构信息（帮助定位新选择器）")

    # 打印 .job-card-wrap 内各子元素的 class 列表（帮助找到职位/公司/薪资的新选择器）
    js_struct = """
    (function() {
        const card = document.querySelector('.job-card-wrap');
        if (!card) return JSON.stringify([]);
        const result = [];
        card.querySelectorAll('*').forEach(el => {
            if (!el.className || typeof el.className !== 'string') return;
            const cls = el.className.trim();
            if (!cls) return;
            const txt = (el.innerText || '').replace(/\\s+/g,' ').trim().slice(0,40);
            result.push({ tag: el.tagName.toLowerCase(), cls, txt });
        });
        return JSON.stringify(result.slice(0, 30));
    })()
    """
    raw = eval_js(tab, js_struct, "卡片内部结构")
    if raw:
        items = json.loads(raw)
        print(f"  {INFO} 第一张卡片内所有子元素（共 {len(items)} 个）：")
        print(f"  {'tag':<6}  {'class':<55}  text")
        print(f"  {'─'*6}  {'─'*55}  {'─'*30}")
        for it in items:
            print(f"  {it['tag']:<6}  {it['cls'][:55]:<55}  {it['txt'][:30]}")

    # JD 面板结构
    subsep("JD 面板（.job-detail-header）直接子元素")
    js_header = """
    (function() {
        const el = document.querySelector('.job-detail-header');
        if (!el) return JSON.stringify([]);
        return JSON.stringify(
            Array.from(el.querySelectorAll('*')).slice(0, 25).map(e => ({
                tag: e.tagName.toLowerCase(),
                cls: (e.className||'').replace(/\\s+/g,' ').trim().slice(0,55),
                txt: (e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,40),
            }))
        );
    })()
    """
    raw2 = eval_js(tab, js_header, "JD面板结构")
    if raw2:
        items2 = json.loads(raw2)
        if items2:
            print(f"  {INFO} .job-detail-header 内部元素（共 {len(items2)} 个）：")
            print(f"  {'tag':<6}  {'class':<55}  text")
            print(f"  {'─'*6}  {'─'*55}  {'─'*30}")
            for it in items2:
                print(f"  {it['tag']:<6}  {it['cls'][:55]:<55}  {it['txt'][:30]}")
        else:
            print(f"  {WARN} .job-detail-header 未找到（JD 面板未打开？）")


# ── 汇总 ──────────────────────────────────────────────────────────────────────

def print_summary():
    sep("缺失选择器汇总（需在代码中更新）", width=64)
    if not missing_report:
        print("  全部选择器正常，无需更新。")
        return
    print(f"  共 {len(missing_report)} 个选择器缺失：\n")
    for sel, loc in missing_report:
        print(f"  选择器: {sel}")
        print(f"  位置:   {loc}")
        print()
    print("  提示：结合 GROUP 5 的结构信息，在 DevTools 中确认新选择器后，")
    print("  将上述位置的旧选择器替换为新选择器，并重新运行本脚本验证。")


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  debug_elements_job.py — scanner 元素检查")
    print("=" * 64)

    tab = connect()
    try:
        check_card_list(tab)
        check_jd_panel(tab)
        check_action_buttons(tab)
        check_card_extraction(tab)
        check_structural_info(tab)
        print_summary()
    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()

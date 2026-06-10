"""
debug_chat_switch.py — 验证会话切换时 window.chat.communicating 读取行为

目的：
  1. 检查 window.chat.communicating 结构及 encryptJobId 可读性
  2. 对比 bare expression 与 IIFE-return 两种写法的差异（复现 handler.py 的历史 bug）
  3. 点击左侧第一个会话卡片，轮询观察 encryptJobId 切换过程

运行前提：
  - start_chrome.bat 已启动（port 9222）
  - 已登录 BOSS直聘，导航到 /web/geek/chat
  - 左侧有至少 2 个会话卡片

用法：
  python src/debug/debug_chat_switch.py
"""
import sys
import time
import json
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL
from shared.cdp_utils import cdp_click, small_human_scroll, scroll_into_view_and_click, SESSION_LI

CDP_URL = CDP_CHAT_URL

OK   = "[OK  ]"
MISS = "[MISS]"
INFO = "[INFO]"
WARN = "[WARN]"


def eval_js(tab, js: str, label: str = "") -> object:
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=10)
        val = raw.get("result", {}).get("value")
        return val
    except Exception as e:
        print(f"  {WARN} JS执行异常 [{label}]: {e}")
        return None


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

    tab_info = next(
        (t for t in tabs_info if "/web/geek/chat" in t.get("url", "") and t.get("type") == "page"), None
    ) or next(
        (t for t in tabs_info if "zhipin.com" in t.get("url", "") and t.get("type") == "page"), None
    )
    if not tab_info:
        print("[失败] 未找到 BOSS直聘 chat 标签页"); sys.exit(1)

    print(f"[标签页] {tab_info.get('title','')[:60]}")
    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == tab_info["id"]), None)
    if not tab:
        print("[失败] pychrome 找不到标签页"); sys.exit(1)
    tab.start()
    print("[CDP] 连接成功\n")
    return tab


# ── GROUP 1: window.chat.communicating 结构检查 ───────────────────────────────

def check_communicating_structure(tab):
    sep("GROUP 1 — window.chat.communicating 结构")

    js = """
    (function() {
        try {
            if (!window.chat) return JSON.stringify({ ok: false, reason: 'window.chat 不存在' });
            const c = window.chat.communicating;
            if (!c) return JSON.stringify({ ok: false, reason: 'window.chat.communicating 为 null/undefined' });
            return JSON.stringify({
                ok          : true,
                encryptJobId: c.encryptJobId  || '',
                companyName : c.companyName   || '',
                name        : c.name          || '',
                jobName     : c.jobName       || '',
                title       : c.title         || '',
                allKeys     : Object.keys(c),
            });
        } catch(e) { return JSON.stringify({ ok: false, reason: String(e) }); }
    })()
    """
    raw = eval_js(tab, js, "communicating 结构")
    if not raw:
        print(f"  {MISS} eval 返回 None（JS 执行失败）")
        return

    d = json.loads(raw)
    if d["ok"]:
        print(f"  {OK} window.chat.communicating 存在")
        print(f"     encryptJobId = {d['encryptJobId']!r}")
        print(f"     companyName  = {d['companyName']!r}")
        print(f"     name         = {d['name']!r}")
        print(f"     jobName      = {d['jobName']!r}")
        print(f"     title        = {d['title']!r}")
        print(f"     全部 keys ({len(d['allKeys'])}): {d['allKeys']}")
    else:
        print(f"  {MISS} {d['reason']}")


# ── GROUP 2: 两种 JS 写法对比 ─────────────────────────────────────────────────

def check_js_syntax(tab):
    sep("GROUP 2 — JS 写法对比（bare expression vs IIFE）")

    cases = [
        (
            "bare expression（正确写法）",
            "(window.chat&&window.chat.communicating&&window.chat.communicating.encryptJobId)||''"
        ),
        (
            "bare return（错误写法，历史 bug）",
            "return (window.chat&&window.chat.communicating&&window.chat.communicating.encryptJobId)||''"
        ),
        (
            "IIFE return（session_processor 写法）",
            "(function(){ try{ const c=window.chat&&window.chat.communicating; if(!c)return null; return c.encryptJobId||''; }catch(e){return null;} })()"
        ),
    ]

    for label, js in cases:
        val = eval_js(tab, js, label)
        status = OK if val is not None else MISS
        print(f"  {status} {label}")
        print(f"     JS   : {js[:80]}")
        print(f"     返回值: {val!r}  (type={type(val).__name__})")
        print()


# ── GROUP 3: 点击切换会话，观察 encryptJobId 变化 ─────────────────────────────

def check_switch_polling(tab):
    sep("GROUP 3 — 点击第二张会话卡片，观察 encryptJobId 切换")

    JS_ID = "(window.chat&&window.chat.communicating&&window.chat.communicating.encryptJobId)||''"

    # 读取切换前的值
    prev_id = eval_js(tab, JS_ID, "切换前 encryptJobId")
    print(f"  {INFO} 切换前 encryptJobId = {prev_id!r}")

    # 获取前两张会话卡片
    js_cards = f"""
    (function() {{
        const lis = Array.from(document.querySelectorAll({json.dumps(SESSION_LI)}));
        return JSON.stringify(lis.slice(0, 5).map((li, idx) => {{
            const r = li.getBoundingClientRect();
            const name = li.querySelector('.name-text');
            const spans = Array.from(li.querySelectorAll('.name-box > span'));
            const href = li.querySelector('a') ? (li.querySelector('a').href||'') : '';
            const m = href.match(/job_detail\\/([^?#]+)/);
            return {{
                idx,
                name         : name ? (name.innerText||'').trim() : '',
                company      : spans.length > 1 ? (spans[1].innerText||'').trim() : '',
                encryptJobId : m ? m[1] : '',
                x            : Math.round(r.left + r.width / 2),
                y            : Math.round(r.top  + r.height / 2),
            }};
        }}));
    }})()
    """
    raw = eval_js(tab, js_cards, "卡片列表")
    if not raw:
        print(f"  {MISS} 无法读取会话卡片列表"); return
    cards = json.loads(raw)
    print(f"  {INFO} 前 {len(cards)} 张卡片：")
    for c in cards:
        print(f"     [{c['idx']}] {c['name']}@{c['company']}  center=({c['x']},{c['y']})")

    # 读当前激活会话的公司名，选第一张不同公司的卡片
    js_cur_company = """
    (function(){
        const c = window.chat && window.chat.communicating;
        return c ? (c.companyName || '') : '';
    })()
    """
    cur_company = eval_js(tab, js_cur_company, "当前公司名") or ""
    target = next(
        (c for c in cards if c.get("company", "") != cur_company),
        cards[-1]   # 兜底取最后一张
    )
    target_idx = target["idx"]
    print(f"\n  {INFO} 点击卡片 [{target_idx}]: {target['name']}@{target['company']}")

    small_human_scroll(tab, lo=80, hi=200)
    locate_js = (f"return Array.from(document.querySelectorAll("
                 f"{json.dumps(SESSION_LI)}))[{target_idx}];")
    clicked = scroll_into_view_and_click(tab, locate_js, delay=None)
    print(f"  {INFO} scroll_into_view_and_click 返回: {clicked}")
    if not clicked:
        cdp_click(tab, target["x"], target["y"])
        print(f"  {INFO} fallback cdp_click ({target['x']},{target['y']})")

    # 轮询观察切换过程
    print(f"\n  {INFO} 开始轮询（最多 10 秒，每 0.3s 一次）：")
    print(f"  {'elapsed':>8}  {'encryptJobId':<36}  status")
    print(f"  {'─'*8}  {'─'*36}  {'─'*20}")

    start = time.time()
    switched = False
    for i in range(34):
        elapsed = time.time() - start
        cur_id = eval_js(tab, JS_ID, f"轮询#{i}")
        if cur_id and cur_id != prev_id:
            print(f"  {elapsed:>8.2f}s  {str(cur_id):<36}  [已切换] ({i+1} 次轮询后)")
            switched = True
            break
        else:
            sym = "→ 变化中（None）" if cur_id is None else f"→ 未变化 {str(cur_id)[:20]!r}"
            print(f"  {elapsed:>8.2f}s  {str(cur_id):<36}  {sym}")
        time.sleep(0.3)

    if not switched:
        cur_id = eval_js(tab, JS_ID, "超时后最终读取")
        print(f"\n  {WARN} 10 秒内未检测到切换。最终值: {cur_id!r}")
    else:
        # 切换后读一次完整 chat_info
        js_full = """
        (function() {
            try {
                const c = window.chat && window.chat.communicating;
                if (!c) return null;
                return JSON.stringify({
                    encryptJobId: c.encryptJobId || '',
                    companyName : c.companyName  || '',
                    name        : c.name         || '',
                    jobName     : c.jobName      || '',
                });
            } catch(e) { return null; }
        })()
        """
        raw2 = eval_js(tab, js_full, "切换后完整 chat_info")
        if raw2:
            d = json.loads(raw2)
            print(f"\n  {OK} 切换后完整信息：")
            for k, v in d.items():
                print(f"     {k:<14} = {v!r}")


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  debug_chat_switch.py — 会话切换 encryptJobId 读取验证")
    print("=" * 64)
    tab = connect()
    try:
        check_communicating_structure(tab)
        check_js_syntax(tab)
        check_switch_polling(tab)
    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()

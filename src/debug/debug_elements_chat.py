"""
debug_elements_chat.py — 检查 chat_handler 使用的所有页面元素

运行前提：
  - start_chrome_chat.bat 已启动（port 9223）
  - 已手动登录 BOSS直聘，导航到 /web/geek/chat
  - 建议打开一个聊天会话（右侧有消息记录），以便检查消息/输入区元素

用法：
  python debug_elements_chat.py

输出：
  每个选择器的状态（OK / MISS）、数量、样本，以及结构信息。
  末尾汇总所有缺失选择器及其代码位置，可直接提供给 Claude Code 修正程序。
"""
import sys
import json
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL

CDP_URL     = CDP_CHAT_URL
SESSION_LI  = ".user-list-content > ul:nth-child(2) > li"

# ── 标记 ──────────────────────────────────────────────────────────────────────
OK   = "[OK  ]"
MISS = "[MISS]"
WARN = "[WARN]"
INFO = "[INFO]"

missing_report: list[tuple[str, str]] = []


def sep(title="", width=64):
    print()
    print("=" * width)
    if title:
        print(f"  {title}")
        print("=" * width)


def subsep(title="", width=64):
    print(f"\n  {'─'*4} {title} {'─'*(width - len(title) - 8)}")


def eval_js(tab, js: str, label: str = ""):
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=10)
        return raw.get("result", {}).get("value")
    except Exception as e:
        print(f"  {WARN} {label} JS错误: {e}")
        return None


def check(tab, sel: str, desc: str, code_loc: str, limit: int = 40) -> int:
    js = f"""
    (function() {{
        const els = Array.from(document.querySelectorAll({json.dumps(sel)}));
        if (!els.length) return JSON.stringify({{ count: 0, samples: [] }});
        const samples = els.slice(0, 3).map(el => {{
            const raw = (el.innerText || el.className || '').trim();
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
        print(f"  {MISS} {sel:<50} JS错误: {e}")
        missing_report.append((sel, code_loc))
        return 0

    count = data["count"]
    samples = data["samples"]
    sample_str = " | ".join(f'"{s}"' for s in samples if s) or "(无文字)"

    if count > 0:
        print(f"  {OK} {sel:<50} count={count:<4} {desc}")
        if samples:
            print(f"         样本: {sample_str}")
    else:
        print(f"  {MISS} {sel:<50} count=0    {desc}  ← 需更新！")
        print(f"         代码位置: {code_loc}")
        missing_report.append((sel, code_loc))

    return count


# ── 连接 CDP ──────────────────────────────────────────────────────────────────

def connect():
    try:
        tabs_info = requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        print(f"[失败] 无法连接 {CDP_URL}: {e}"); sys.exit(1)

    im = next(
        (t for t in tabs_info
         if "/web/geek/chat" in t.get("url", "") and t.get("type") == "page"), None
    ) or next(
        (t for t in tabs_info
         if "zhipin.com" in t.get("url", "") and t.get("type") == "page"), None
    )
    if not im:
        print("[失败] 未找到 BOSS直聘 chat 标签页"); sys.exit(1)

    print(f"[标签页] {im.get('title','')[:60]}")
    print(f"[URL]    {im.get('url','')}")

    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == im["id"]), None)
    if not tab:
        print("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    print("[CDP] 连接成功")
    return tab


# ── 检查函数 ──────────────────────────────────────────────────────────────────

def check_session_list(tab):
    sep("GROUP 1 — 左侧会话列表")

    n_list = check(tab, ".user-list-content",
                   "会话列表滚动容器",
                   "handler.py: _JS_GET_SESSIONS / small_human_scroll 坐标基准")

    n_li = check(tab, SESSION_LI,
                 "单个会话卡片（SESSION_LI 常量）",
                 "handler.py: SESSION_LI 常量 / _JS_GET_SESSIONS")

    if n_li == 0:
        # 尝试备选结构
        print(f"\n  {WARN} 尝试备选结构定位会话卡片：")
        for alt_sel in [
            ".user-list-content li",
            ".user-list-content > ul > li",
            ".session-list li",
            "[class*='chat-list'] li",
            "[class*='user-list'] li",
        ]:
            n = eval_js(tab, f"document.querySelectorAll({json.dumps(alt_sel)}).length", alt_sel)
            if n and int(n) > 0:
                print(f"  {OK} 备选: {alt_sel}  count={n}  ← 可替换 SESSION_LI")
            else:
                print(f"  {MISS} 备选: {alt_sel}  count=0")

    check(tab, ".name-text",
          "会话卡片 - 姓名",
          "handler.py: _JS_GET_SESSIONS q('.name-text')")

    check(tab, ".name-box > span",
          "会话卡片 - name-box 内所有 span（公司/职位）",
          "handler.py: _JS_GET_SESSIONS spans 数组（[1]=公司 [last]=职位）")

    check(tab, ".time",
          "会话卡片 - 时间",
          "handler.py: _JS_GET_SESSIONS q('.time')")

    check(tab, ".last-msg-text",
          "会话卡片 - 最新消息预览",
          "handler.py: _JS_GET_SESSIONS q('.last-msg-text')")

    check(tab, ".notice-badge",
          "会话卡片 - 未读角标",
          "handler.py: _JS_GET_SESSIONS q('.notice-badge')")

    # 读取前3个会话的完整字段
    subsep("会话列表字段提取（前3个）")
    js_sessions = f"""
    (function() {{
        const lis = Array.from(document.querySelectorAll({json.dumps(SESSION_LI)}));
        return JSON.stringify(lis.slice(0, 3).map((li, idx) => {{
            const q     = s => {{ const e = li.querySelector(s); return e ? (e.innerText||'').trim() : ''; }};
            const spans = Array.from(li.querySelectorAll('.name-box > span'));
            const r     = li.getBoundingClientRect();
            return {{
                idx,
                name    : q('.name-text'),
                company : spans.length > 1 ? (spans[1].innerText||'').trim() : '(无)',
                title   : spans.length > 2 ? (spans[spans.length-1].innerText||'').trim() : '(无)',
                time    : q('.time'),
                preview : q('.last-msg-text'),
                unread  : q('.notice-badge'),
                inView  : r.top >= 0 && r.bottom <= window.innerHeight,
            }};
        }}));
    }})()
    """
    raw = eval_js(tab, js_sessions, "会话列表提取")
    if raw:
        sessions = json.loads(raw)
        print(f"  {INFO} 前 {len(sessions)} 个会话提取结果：")
        for s in sessions:
            status = OK if s["name"] else WARN
            print(f"\n  {status} 会话[{s['idx']}]  inView={s['inView']}")
            for f in ["name", "company", "title", "time", "preview", "unread"]:
                val = s.get(f, "")
                st  = OK if val else WARN
                print(f"    {st} {f:<10} = {val!r}")


def check_chat_messages(tab):
    sep("GROUP 2 — 右侧聊天消息区")

    n_chat = check(tab, ".chat-content",
                   "消息容器",
                   "session_processor.py / cdp_utils.py: _JS_READ_MESSAGES")
    if n_chat == 0:
        print(f"  {WARN} 未找到 .chat-content，请先打开一个聊天会话")

    check(tab, ".message-item",
          "单条消息",
          "cdp_utils.py: _JS_READ_MESSAGES querySelectorAll('.message-item')")

    # 消息分类统计
    subsep("消息分类统计（isSelf / isSystem / isCard）")
    js_classify = """
    (function() {
        const items = Array.from(document.querySelectorAll('.message-item'));
        let myself=0, friend=0, system=0, card=0, interactive=0;
        items.forEach(msg => {
            const cls = (msg.className||'').toString();
            const isSelf   = cls.includes('item-myself');
            const hasCard  = !!msg.querySelector('.message-card-wrap');
            const hasArt   = !!msg.querySelector('.articles-center');
            const isSystem = cls.includes('item-system') || hasArt;
            const isCard   = hasCard || hasArt;
            const btns     = msg.querySelectorAll('.card-btn');
            const hasAgree = Array.from(btns).some(b => b.innerText.trim()==='同意'
                                                        && !b.classList.contains('disabled'));
            if (isSelf)   myself++;
            else if (isSystem) system++;
            else          friend++;
            if (isCard)   card++;
            if (hasAgree) interactive++;
        });
        return JSON.stringify({ total: items.length, myself, friend, system, card, interactive });
    })()
    """
    raw = eval_js(tab, js_classify, "消息分类")
    if raw:
        d = json.loads(raw)
        print(f"  {INFO} 消息总数={d['total']}  我方={d['myself']}  HR={d['friend']}  "
              f"系统={d['system']}  卡片={d['card']}  可点同意={d['interactive']}")

    check(tab, ".message-card-wrap",
          "卡片消息包装（简历/微信等卡片）",
          "cdp_utils.py: _JS_READ_MESSAGES hasCardWrap / session_actions.py: _JS_FIND_AGREE_CARDS")

    check(tab, ".articles-center",
          "系统 PK 卡片（用于识别 isSystem）",
          "cdp_utils.py: _JS_READ_MESSAGES hasArticles")

    check(tab, ".card-btn",
          "卡片按钮（同意/拒绝/兴趣）",
          "cdp_utils.py: _JS_READ_MESSAGES cardBtns / session_actions.py: _JS_FIND_AGREE_CARDS")

    # 消息文字提取选择器
    subsep("消息文字提取")
    check(tab, ".text p",
          "HR/我方普通文字消息（主选）",
          "cdp_utils.py: _JS_READ_MESSAGES textEl = msg.querySelector('.text p')")

    check(tab, ".hyper-link",
          "系统提示文字（.hyper-link 备选）",
          "cdp_utils.py: _JS_READ_MESSAGES textEl 备选 || .hyper-link")

    check(tab, ".message-content",
          "消息内容兜底选择器",
          "cdp_utils.py: _JS_READ_MESSAGES msg.querySelector('.message-content') 兜底")

    check(tab, ".time",
          "消息时间",
          "cdp_utils.py: _JS_READ_MESSAGES timeEl")

    check(tab, ".message-status",
          "消息状态（已读/发送中等）",
          "cdp_utils.py: _JS_READ_MESSAGES statusEl")

    check(tab, ".message-card-top-title",
          "卡片标题（识别卡片类型文字）",
          "cdp_utils.py: _JS_READ_MESSAGES cardTitle")

    # item-myself / item-friend / item-system class 验证
    subsep("消息 class 命名验证")
    js_cls = """
    (function() {
        const all = Array.from(document.querySelectorAll('.message-item'));
        const classes = new Set();
        all.forEach(el => {
            (el.className||'').split(/\\s+/).filter(c =>
                c.startsWith('item-') || c.includes('message')
            ).forEach(c => classes.add(c));
        });
        return JSON.stringify(Array.from(classes).sort());
    })()
    """
    raw2 = eval_js(tab, js_cls, "message-item class")
    if raw2:
        cls_list = json.loads(raw2)
        print(f"  {INFO} .message-item 的 class 变体：{cls_list}")
        for expected in ["item-myself", "item-friend", "item-system"]:
            st = OK if expected in cls_list else MISS
            print(f"    {st} {expected}")
            if expected not in cls_list:
                missing_report.append((f".message-item.{expected}",
                                       "cdp_utils.py: _JS_READ_MESSAGES isSelf / isSystem 判断"))


def check_card_types(tab):
    sep("GROUP 3 — 交互卡片类型识别")

    check(tab, "span.dialog-icon",
          "卡片图标（简历/微信/沟通意向）",
          "session_actions.py: _JS_FIND_AGREE_CARDS wrap.querySelector('span.dialog-icon')")

    check(tab, "span.concat-icon",
          "联系卡片图标（微信号展示卡）",
          "session_actions.py: _JS_FIND_AGREE_CARDS wrap.querySelector('span.concat-icon')")

    # 识别各类卡片的 class
    subsep("dialog-icon / concat-icon 次级 class（决定卡片类型）")
    js_icons = """
    (function() {
        const result = [];
        document.querySelectorAll('.message-card-wrap').forEach(wrap => {
            const di = wrap.querySelector('span.dialog-icon');
            const ci = wrap.querySelector('span.concat-icon');
            const el = di || ci;
            if (!el) return;
            const kind = di ? 'dialog-icon' : 'concat-icon';
            const parts = (el.className||'').trim().split(/\\s+/);
            const sub = parts.find(c => c !== kind) || '(无次级class)';
            const title = wrap.querySelector('.message-card-top-title');
            result.push({
                kind, sub,
                title: title ? (title.innerText||'').trim().slice(0,40) : '',
                hasAgreeBtn: Array.from(wrap.querySelectorAll('.card-btn'))
                                  .some(b => b.innerText.trim()==='同意'
                                             && !b.classList.contains('disabled')),
            });
        });
        return JSON.stringify(result);
    })()
    """
    raw = eval_js(tab, js_icons, "icon分类")
    if raw:
        icons = json.loads(raw)
        if icons:
            print(f"  {INFO} 当前页面卡片：")
            for ic in icons:
                print(f"    {ic['kind']}.{ic['sub']:<12}  title={ic['title']!r:<30}  "
                      f"可点同意={ic['hasAgreeBtn']}")
            expected_types = {"resume": "简历请求", "weixin": "微信交换", "note": "沟通意向"}
            found_subs = {ic["sub"] for ic in icons}
            for t, label in expected_types.items():
                st = OK if t in found_subs else INFO
                print(f"    {st} {label:<10} (sub={t!r}) {'存在' if t in found_subs else '当前页面未见'}")
        else:
            print(f"  {INFO} 当前页面无交互卡片（正常）")


def check_action_area(tab):
    sep("GROUP 4 — 操作区（输入框 / 发送 / 工具栏）")

    n_input = check(tab, "div.chat-input[contenteditable='true']",
                    "文字输入框",
                    "session_actions.py: INPUT_SEL 常量 / clear_and_type")

    check(tab, "button.btn-send",
          "发送按钮",
          "session_actions.py: SEND_SEL 常量 / click_send")

    check(tab, ".toolbar-btn-content",
          "工具栏按钮（发简历/换电话等）",
          "session_actions.py: click_resume_btn querySelectorAll('.toolbar-btn-content')")

    # 工具栏按钮文字列表
    js_toolbar = """
    (function() {
        return JSON.stringify(
            Array.from(document.querySelectorAll('.toolbar-btn-content'))
                 .map(el => (el.innerText||'').trim())
        );
    })()
    """
    raw = eval_js(tab, js_toolbar, "工具栏按钮")
    if raw:
        btns = json.loads(raw)
        if btns:
            print(f"  {INFO} 工具栏按钮文字: {btns}")
            st = OK if "发简历" in btns or any("简历" in b for b in btns) else MISS
            print(f"    {st} 「发简历」{'存在' if st==OK else '未找到 ← 需更新工具栏文字匹配'}")
            if st == MISS:
                missing_report.append((".toolbar-btn-content text='发简历'",
                                       "session_actions.py: click_resume_btn txt === '发简历'"))
        else:
            print(f"  {WARN} 工具栏按钮列表为空（需打开聊天会话）")

    check(tab, "div.btn-contact",
          "换电话按钮",
          "CLAUDE.md 选择器表: div.btn-contact")

    check(tab, "div.btn-weixin",
          "换微信按钮",
          "CLAUDE.md 选择器表: div.btn-weixin")

    # 输入框内部状态
    if n_input > 0:
        subsep("输入框状态")
        js_input = """
        (function() {
            const el = document.querySelector("div.chat-input[contenteditable='true']");
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return JSON.stringify({
                editable : el.getAttribute('contenteditable'),
                text     : (el.innerText||'').slice(0,40),
                scrollH  : el.scrollHeight,
                clientH  : el.clientHeight,
                center   : { x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2) },
            });
        })()
        """
        raw2 = eval_js(tab, js_input, "输入框状态")
        if raw2:
            d = json.loads(raw2)
            print(f"  {INFO} contenteditable={d['editable']!r}  "
                  f"scrollH={d['scrollH']}  clientH={d['clientH']}  "
                  f"center=({d['center']['x']},{d['center']['y']})")
            print(f"  {INFO} 当前内容: {d['text']!r}")

    # 发送按钮状态
    js_send = f"""
    (function() {{
        const btn = document.querySelector({json.dumps("button.btn-send")});
        if (!btn) return null;
        const r = btn.getBoundingClientRect();
        return JSON.stringify({{
            disabled : btn.classList.contains('disabled'),
            text     : (btn.innerText||'').trim(),
            center   : {{ x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2) }},
        }});
    }})()
    """
    raw3 = eval_js(tab, js_send, "发送按钮状态")
    if raw3:
        d = json.loads(raw3)
        print(f"  {INFO} 发送按钮: disabled={d['disabled']}  "
              f"text={d['text']!r}  center=({d['center']['x']},{d['center']['y']})")


def check_resume_popup(tab):
    sep("GROUP 5 — 简历选择弹窗（需先点击「发简历」才会出现）")

    n_popup = check(tab, ".boss-popup__wrapper",
                    "简历弹窗容器",
                    "session_actions.py: handle_resume_dialog querySelector('.boss-popup__wrapper')")

    if n_popup == 0:
        print(f"  {INFO} 弹窗当前未出现（正常），下列选择器在弹窗打开后才能验证")
        # 仍然打印说明
        for sel, desc, loc in [
            ("span.resume-name", "简历列表项（含「袁柯」）",
             "session_actions.py: handle_resume_dialog querySelectorAll('span.resume-name')"),
            (".btn-confirm", "弹窗确认/发送按钮",
             "session_actions.py: handle_resume_dialog querySelector('.btn-confirm')"),
        ]:
            print(f"  {INFO} {sel:<35} （{desc}）→ {loc}")
        return

    check(tab, "span.resume-name",
          "简历列表项",
          "session_actions.py: handle_resume_dialog querySelectorAll('span.resume-name')")

    check(tab, ".btn-confirm",
          "弹窗确认/发送按钮",
          "session_actions.py: handle_resume_dialog querySelector('.btn-confirm')")

    # 查找「袁柯」
    js_find = """
    (function() {
        const items = Array.from(document.querySelectorAll('span.resume-name'));
        return JSON.stringify(items.map(el => ({
            text: (el.innerText||'').trim(),
            cls : (el.className||'').trim(),
        })));
    })()
    """
    raw = eval_js(tab, js_find, "简历列表")
    if raw:
        items = json.loads(raw)
        print(f"  {INFO} 简历列表项（共 {len(items)} 个）：{items}")
        found = any("袁柯" in it["text"] for it in items)
        print(f"  {OK if found else MISS} 含「袁柯」的简历项: {'找到' if found else '未找到，需确认简历名称'}")


def check_global_info(tab):
    sep("GROUP 6 — 全局信息")

    # window.chat.communicating
    js_comm = """
    (function() {
        try {
            const c = window.chat && window.chat.communicating;
            if (!c) return JSON.stringify({ ok: false, reason: 'window.chat.communicating 不存在' });
            return JSON.stringify({
                ok          : true,
                name        : c.name || '',
                companyName : c.companyName || '',
                jobName     : c.jobName || '',
                encryptJobId: c.encryptJobId || '',
                keys        : Object.keys(c).slice(0, 20),
            });
        } catch(e) {
            return JSON.stringify({ ok: false, reason: String(e) });
        }
    })()
    """
    raw = eval_js(tab, js_comm, "window.chat.communicating")
    if raw:
        d = json.loads(raw)
        if d["ok"]:
            print(f"  {OK} window.chat.communicating 存在")
            print(f"         name={d['name']!r}  company={d['companyName']!r}")
            print(f"         jobName={d['jobName']!r}  encryptJobId={d['encryptJobId']!r}")
            print(f"         全部 key: {d['keys']}")
        else:
            print(f"  {MISS} window.chat.communicating 不可用: {d['reason']}")
            missing_report.append(("window.chat.communicating",
                                   "session_processor.py: get_current_chat_info → JS window.chat.communicating"))

    # 未读数
    check(tab, "span.nav-chat-num",
          "顶导未读总数",
          "CLAUDE.md 选择器表: span.nav-chat-num")


def check_structural_info(tab):
    sep("GROUP 7 — 辅助结构信息（帮助定位新选择器）")

    # 会话卡片内部结构
    subsep("左侧第一张会话卡片内部元素")
    js_li = f"""
    (function() {{
        const li = document.querySelector({json.dumps(SESSION_LI)});
        if (!li) return JSON.stringify([]);
        return JSON.stringify(
            Array.from(li.querySelectorAll('*')).map(e => ({{
                tag : e.tagName.toLowerCase(),
                cls : (e.className||'').replace(/\\s+/g,' ').trim().slice(0,55),
                txt : (e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,35),
            }}))
        );
    }})()
    """
    raw = eval_js(tab, js_li, "会话卡片结构")
    if raw:
        items = json.loads(raw)
        if items:
            print(f"  {INFO} 共 {len(items)} 个子元素：")
            print(f"  {'tag':<6}  {'class':<55}  text")
            print(f"  {'─'*6}  {'─'*55}  {'─'*30}")
            for it in items[:20]:
                print(f"  {it['tag']:<6}  {it['cls'][:55]:<55}  {it['txt'][:30]}")
            if len(items) > 20:
                print(f"  ... 共 {len(items)} 个，只显示前20")
        else:
            print(f"  {WARN} 未找到会话卡片（SESSION_LI 可能已失效）")

    # 消息容器内第一条消息结构
    subsep("第一条 .message-item 内部结构")
    js_msg = """
    (function() {
        const msg = document.querySelector('.message-item');
        if (!msg) return JSON.stringify({ cls: '', children: [] });
        return JSON.stringify({
            cls: msg.className,
            children: Array.from(msg.querySelectorAll('*')).slice(0, 20).map(e => ({
                tag : e.tagName.toLowerCase(),
                cls : (e.className||'').replace(/\\s+/g,' ').trim().slice(0,55),
                txt : (e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,35),
            }))
        });
    })()
    """
    raw2 = eval_js(tab, js_msg, "消息结构")
    if raw2:
        d = json.loads(raw2)
        print(f"  {INFO} 第一条消息 class: {d['cls']!r}")
        print(f"  {'tag':<6}  {'class':<55}  text")
        print(f"  {'─'*6}  {'─'*55}  {'─'*30}")
        for it in d["children"]:
            print(f"  {it['tag']:<6}  {it['cls'][:55]:<55}  {it['txt'][:30]}")


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
    print("  提示：结合 GROUP 7 的结构信息，在 DevTools 中确认新选择器后，")
    print("  将上述位置的旧选择器替换为新选择器，并重新运行本脚本验证。")


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  debug_elements_chat.py — chat_handler 元素检查")
    print("=" * 64)

    tab = connect()
    try:
        check_session_list(tab)
        check_chat_messages(tab)
        check_card_types(tab)
        check_action_area(tab)
        check_resume_popup(tab)
        check_global_info(tab)
        check_structural_info(tab)
        print_summary()
    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()

"""
debug_resume_attachment_flow.py — 「发简历→管理附件→上传/删除附件」全流程探索测试

完整链路（用户描述，按顺序）：
  1.  聊天页点击「发简历」按钮 → 出现弹窗
  2.  点击弹窗里的「管理附件」 → 当前标签页跳转到附件管理页面
  3.  附件管理页找到「+」号按钮并点击 → 出现下拉列表
  4.  点击下拉列表里的「上传简历」 → 出现弹窗
  5.  点击弹窗里的「上传附件简历」 → 触发系统原生文件选择器（CDP 拦截绕过）
  6.  注入测试文件，完成"选择"
  7.  出现「确认添加」弹窗 → 点击确认
  8.  出现新弹窗 → 点击右上角「×」关闭
  9.  回退到聊天页（浏览器历史回退）
  10. 重新点击刚才那个岗位 ID 对应的聊天卡片
  11. 点击「发简历」按钮 → 出弹窗 → 点击「×」关闭
  12. 点击右上角「简历」按钮，找到「附件管理」框，定位刚上传的附件
  13. 点击该附件右侧「…」（三个点）→ 出现菜单 → 点击「删除」
  14. 出现确认弹窗 → 点击「确定」
  15. 回退到聊天页，重新点击该岗位的聊天卡片，结束流程

这是一个**探索性测试脚本**：多数选择器目前未知，需根据真实页面反馈迭代调整。
每一步失败都会打印失败原因 + 当前页面相关 DOM 片段，方便定位问题。

不修改任何主流程代码（chat_handler.py / session_actions.py / session_processor.py）。

运行前提：
  - start_chrome_chat.bat 已启动（port 9223）
  - 已登录 BOSS直聘，导航到 /web/geek/chat，并打开一个有「发简历」按钮的会话

用法：
  python src/debug/debug_resume_attachment_flow.py
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import time
import json
import threading
import requests
import pychrome
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL
from shared.cdp_utils import evaluate, cdp_click, random_delay

CDP_URL    = CDP_CHAT_URL
SESSION_LI = ".user-list-content > ul:nth-child(2) > li"

OK   = "[OK  ]"
MISS = "[MISS]"
INFO = "[INFO]"
WARN = "[WARN]"
STEP = "[STEP]"

# ── 测试文件 ──────────────────────────────────────────────────────────────────
# 放在 src/debug/ 下，文件名带时间戳标记，方便在附件列表中精确识别 + 之后删除
_TS         = datetime.now().strftime("%Y%m%d_%H%M%S")
TEST_MARK   = f"测试附件_{_TS}"
TEST_FILE   = Path(__file__).parent / f"test_attachment_{_TS}.pdf"


def _write_minimal_pdf(path: Path, text: str):
    """
    生成一个最小可用 PDF（不引入 reportlab 等新依赖）。
    多数文件选择器/上传控件只校验扩展名和文件头(%PDF)，不会做严格语法解析；
    若目标网站对 PDF 做了服务端严格校验导致上传失败，可把 TEST_FILE 后缀
    改成 .txt 并改用纯文本写入（见下方注释）。
    """
    content = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET"
    pdf = (
        "%PDF-1.4\n"
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        "4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        f"5 0 obj<</Length {len(content)}>>stream\n{content}\nendstream endobj\n"
        "trailer<</Size 6/Root 1 0 R>>\n"
        "%%EOF"
    )
    path.write_bytes(pdf.encode("latin-1", errors="replace"))
    # 纯文本版本（如需切换）：
    # path.write_text(text, encoding="utf-8")


def cdp_hover(tab, x: float, y: float):
    """发送 mouseMoved 事件，触发 hover 态（部分下拉菜单是 :hover 展开而非 click 切换）。"""
    tab.call_method("Input.dispatchMouseEvent", type="mouseMoved",
                    x=x, y=y, button="none", modifiers=0)


def sep(title="", width=72):
    print()
    print("=" * width)
    if title:
        print(f"  {title}")
        print("=" * width)


# ── CDP 连接 ──────────────────────────────────────────────────────────────────

def list_tabs() -> list[dict]:
    return requests.get(f"{CDP_URL}/json", timeout=5).json()


def find_chat_tab() -> dict | None:
    for t in list_tabs():
        if t.get("type") == "page" and "/web/geek/chat" in t.get("url", ""):
            return t
    return None


# ── 通用：按文本查找可点击元素并返回坐标 ──────────────────────────────────────

def find_by_text(tab, text: str, container_sel: str | None = None,
                 exact: bool = False, max_w: int = 700) -> dict | None:
    """
    在 container_sel（或全文档）范围内查找 innerText 包含/等于 text 的最小可点击元素，
    返回 {x, y, txt, cls} 或 None。用于探索未知选择器的场景。
    """
    root_expr = (f"document.querySelector({json.dumps(container_sel)})"
                 if container_sel else "document.body")
    cmp_expr = (f"txt === {json.dumps(text)}" if exact
                else f"txt.includes({json.dumps(text)})")
    js = f"""
    (function() {{
        const root = {root_expr};
        if (!root) return null;
        let best = null, bestScore = -999;
        const all = root.querySelectorAll('*');
        for (const el of all) {{
            const txt = (el.innerText || el.textContent || '').trim();
            if (!txt || txt.length > 60) continue;
            if (!({cmp_expr})) continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > {max_w}) continue;
            // 优先选择"自身文本等于/最接近搜索词"的节点，避免命中把多个按钮文案拼接在一起的父容器
            const exactBonus = (txt === {json.dumps(text)}) ? 100 : 0;
            const lenPenalty = Math.abs(txt.length - {len(text)});
            const score = exactBonus + (el.children.length === 0 ? 20 : 0)
                        - el.children.length * 3 - lenPenalty * 4;
            if (score > bestScore) {{
                bestScore = score;
                best = {{
                    x: Math.round(r.left + r.width / 2),
                    y: Math.round(r.top  + r.height / 2),
                    txt: txt.slice(0, 40),
                    cls: (el.className || '').toString().replace(/\\s+/g,' ').trim().slice(0, 80),
                    tag: el.tagName,
                }};
            }}
        }}
        return best ? JSON.stringify(best) : null;
    }})()
    """
    val = evaluate(tab, js)
    if not val or val == "null":
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


def find_by_text_excluding(tab, text: str, exclude: list[str],
                           container_sel: str | None = None, max_w: int = 700) -> dict | None:
    """
    查找包含 text 但不包含 exclude 中任一文案的最小可点击元素。
    用于区分"并列菜单项被同一容器的 innerText 拼接在一起"的情况
    （例如下拉菜单容器的 innerText 同时含「上传简历」和「制作附件简历」）。
    """
    root_expr = (f"document.querySelector({json.dumps(container_sel)})"
                 if container_sel else "document.body")
    js = f"""
    (function() {{
        const root = {root_expr};
        if (!root) return null;
        const want = {json.dumps(text)};
        const excl = {json.dumps(exclude)};
        let best = null, bestScore = -999;
        for (const el of root.querySelectorAll('*')) {{
            const txt = (el.innerText || el.textContent || '').trim();
            if (!txt || txt.length > 60 || !txt.includes(want)) continue;
            if (excl.some(e => txt.includes(e))) continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > {max_w}) continue;
            const lenPenalty = Math.abs(txt.length - want.length);
            const score = (el.children.length === 0 ? 20 : 0) - el.children.length * 3 - lenPenalty * 4;
            if (score > bestScore) {{
                bestScore = score;
                best = {{x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
                         txt: txt.slice(0, 40),
                         cls: (el.className||'').toString().replace(/\\s+/g,' ').trim().slice(0, 80),
                         tag: el.tagName}};
            }}
        }}
        return best ? JSON.stringify(best) : null;
    }})()
    """
    val = evaluate(tab, js)
    if not val or val == "null":
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


def click_text(tab, text: str, container_sel: str | None = None,
               exact: bool = False, label: str | None = None) -> bool:
    label = label or text
    item = find_by_text(tab, text, container_sel=container_sel, exact=exact)
    if not item:
        print(f"  {MISS} 未找到「{label}」"
              f"{f'（容器 {container_sel}）' if container_sel else ''}")
        dump_dom_hint(tab, container_sel)
        return False
    print(f"  {OK} 找到「{label}」: text={item['txt']!r} tag={item['tag']} "
          f"cls={item['cls']!r} center=({item['x']},{item['y']})")
    cdp_click(tab, item["x"], item["y"])
    return True


def dump_dom_hint(tab, container_sel: str | None = None, limit: int = 25):
    """失败时打印候选区域的可见文本元素，辅助人工调整选择器。"""
    root_expr = (f"document.querySelector({json.dumps(container_sel)})"
                 if container_sel else "document.body")
    js = f"""
    (function() {{
        const root = {root_expr};
        if (!root) return JSON.stringify([]);
        const out = [];
        root.querySelectorAll('*').forEach(el => {{
            if (out.length >= {limit}) return;
            const txt = (el.innerText || '').trim();
            if (!txt || txt.length > 30 || el.children.length > 2) return;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            out.push({{txt: txt.slice(0,30), cls: (el.className||'').toString().slice(0,50), tag: el.tagName}});
        }});
        return JSON.stringify(out);
    }})()
    """
    val = evaluate(tab, js)
    try:
        items = json.loads(val) if val else []
    except Exception:
        items = []
    if items:
        print(f"  {INFO} 候选区域可见文本元素（最多{limit}个，供人工核对选择器）：")
        for it in items:
            print(f"        text={it['txt']!r:32} tag={it['tag']:6} cls={it['cls']!r}")


# ── 通用：等待条件成立 ────────────────────────────────────────────────────────

def wait_until(tab, js_cond: str, timeout: float = 8.0, interval: float = 0.5,
               desc: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if evaluate(tab, js_cond):
            return True
        time.sleep(interval)
    if desc:
        print(f"  {WARN} 等待超时: {desc}")
    return False


def wait_popup_visible(tab, selectors: list[str], timeout: float = 8.0) -> str | None:
    """轮询多个候选弹窗选择器，返回首个可见的选择器，否则 None。"""
    js = f"""
    (function() {{
        const sels = {json.dumps(selectors)};
        for (const sel of sels) {{
            const els = document.querySelectorAll(sel);
            for (const el of els) {{
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0)
                    return sel;
            }}
        }}
        return null;
    }})()
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = evaluate(tab, js)
        if val and val != "null":
            return val
        time.sleep(0.4)
    return None


# ── 步骤 1~2：发简历 → 管理附件（当前标签页跳转）──────────────────────────────

def step_open_send_resume_dialog(tab) -> bool:
    sep("STEP 1 — 点击「发简历」按钮，等待弹窗")
    js_btn = """
    (function() {
        for (const el of document.querySelectorAll('.toolbar-btn-content')) {
            const txt = (el.innerText || '').trim();
            if (txt.includes('发简历')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    return JSON.stringify({x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)});
            }
        }
        return null;
    })()
    """
    val = evaluate(tab, js_btn)
    if not val or val == "null":
        print(f"  {MISS} 未找到「发简历」工具栏按钮")
        return False
    btn = json.loads(val)
    print(f"  {OK} 点击「发简历」按钮 center=({btn['x']},{btn['y']})")
    cdp_click(tab, btn["x"], btn["y"])
    random_delay(1.0, 1.5)

    sel = wait_popup_visible(tab, [".boss-popup__wrapper", "[class*='popup']", "[class*='modal']"])
    if not sel:
        print(f"  {MISS} 未检测到弹窗")
        return False
    print(f"  {OK} 弹窗出现: {sel}")
    return True


def step_click_manage_attachment(tab) -> bool:
    sep("STEP 2 — 点击弹窗里的「管理附件」，等待页面跳转")
    url_before = evaluate(tab, "window.location.href") or ""
    if not click_text(tab, "管理附件", container_sel=".boss-popup__wrapper"):
        # 容器选择器可能不对，退化到全文档查找
        if not click_text(tab, "管理附件"):
            return False
    random_delay(1.0, 1.5)

    ok = wait_until(tab, f"window.location.href !== {json.dumps(url_before)}",
                    timeout=10, desc="页面跳转到附件管理页")
    if not ok:
        print(f"  {MISS} 未检测到 URL 变化，当前: {evaluate(tab, 'window.location.href')}")
        return False
    wait_until(tab, "document.readyState === 'complete'", timeout=10, desc="附件管理页加载完成")
    time.sleep(1.0)
    print(f"  {OK} 已跳转，当前 URL: {evaluate(tab, 'window.location.href')}")
    return True


# ── 步骤 3~4：附件管理页 → 「+」→「上传简历」───────────────────────────────────

def step_click_plus_and_upload_resume(tab) -> bool:
    sep("STEP 3 — 点击「+」号按钮，等待下拉列表")
    # 已验证选择器：「+」触发器实际是 a.sider-title-operate（紧贴"附件管理"标题右侧，
    # hover/点击展开含"上传简历"/"制作附件简历"的下拉菜单）
    js_direct = """
    (function() {
        const el = document.querySelector('.resume-attachment a.sider-title-operate')
                || document.querySelector('a.sider-title-operate');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        return JSON.stringify({x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
                               w: Math.round(r.width), h: Math.round(r.height),
                               cls: (el.className||'').toString(), tag: el.tagName,
                               txt: (el.innerText||'').slice(0,30)});
    })()
    """
    direct = evaluate(tab, js_direct)
    if direct and direct != "null":
        btn = json.loads(direct)
        print(f"  {OK} 直接命中 a.sider-title-operate: txt={btn['txt']!r} "
              f"size=({btn['w']}x{btn['h']}) center=({btn['x']},{btn['y']})")
        cdp_hover(tab, btn["x"], btn["y"])
        time.sleep(0.6)
        item = (find_by_text(tab, "上传简历", exact=True)
                or find_by_text_excluding(tab, "上传简历", exclude=["制作附件简历"]))
        if not item:
            print(f"  {INFO} 悬浮未触发菜单，尝试点击")
            cdp_click(tab, btn["x"], btn["y"])
            random_delay(0.8, 1.3)
            item = (find_by_text(tab, "上传简历", exact=True)
                    or find_by_text_excluding(tab, "上传简历", exclude=["制作附件简历"]))
        sep("STEP 4 — 点击下拉列表里的「上传简历」，等待弹窗")
        if not item:
            print(f"  {MISS} 未找到「上传简历」菜单项（悬浮和点击均未展开菜单）")
            dump_dom_hint(tab)
            return False
        print(f"  {OK} 找到「上传简历」: text={item['txt']!r} center=({item['x']},{item['y']})")
        cdp_hover(tab, item["x"], item["y"])
        time.sleep(0.2)
        cdp_click(tab, item["x"], item["y"])
        random_delay(1.0, 1.6)
        return True

    js_plus = """
    (function() {
        const visible = (r) => r.width > 0 && r.height > 0;
        const all = Array.from(document.querySelectorAll('*'));

        // 第一步：找到「附件管理」标题文本节点本身（最小的含该文案的元素）
        let title = null, titleArea = Infinity;
        for (const el of all) {
            const txt = (el.innerText || el.textContent || '').trim();
            if (txt !== '附件管理') continue;
            const r = el.getBoundingClientRect();
            if (!visible(r)) continue;
            const area = r.width * r.height;
            if (area < titleArea) { title = el; titleArea = area; }
        }
        if (!title) return JSON.stringify({error: 'no-title'});
        const tr = title.getBoundingClientRect();

        // 第二步：在标题元素自身、其内部子元素、右侧同一行、或其正下方的
        // 小尺寸可点击元素中找「+」（不依赖 class 命名，按几何/文案特征筛选）
        const cands = [];
        for (const el of all) {
            const r = el.getBoundingClientRect();
            if (!visible(r)) continue;
            if (r.width > 60 || r.height > 60) continue;            // 图标按钮通常很小
            const txt = (el.innerText || el.textContent || '').trim();
            const isPlusTxt = (txt === '+' || txt === '＋' || txt === '十');
            const cls = (el.className||'').toString();
            const isPlusIcon = /add|plus|icon-add/i.test(cls);

            const sameRow  = Math.min(r.bottom, tr.bottom) - Math.max(r.top, tr.top) > Math.min(r.height, tr.height) * 0.3;
            const toRight  = r.left >= tr.left - 2 && (r.left - tr.right) < 600;
            const below    = r.top >= tr.bottom - 2 && (r.top - tr.bottom) < 200 && Math.abs(r.left - tr.left) < 600;
            const inside   = title.contains(el) || el.contains(title);

            if (!(inside || (sameRow && toRight) || below)) continue;
            // 必须像个按钮：要么文案是"+"，要么 class 暗示 add/plus，要么是空文案的小方块（图标）
            if (!isPlusTxt && !isPlusIcon && txt.length > 0) continue;

            cands.push({
                x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
                w: Math.round(r.width), h: Math.round(r.height),
                dx: Math.round(r.left - tr.right), dy: Math.round(r.top - tr.bottom),
                txt: txt.slice(0, 10),
                cls: cls.toString().replace(/\\s+/g,' ').trim().slice(0, 60),
                tag: el.tagName,
                isPlusTxt, isPlusIcon,
            });
        }
        if (!cands.length) return JSON.stringify({error: 'no-candidate', titleRect: {x: Math.round(tr.left), y: Math.round(tr.top), w: Math.round(tr.width), h: Math.round(tr.height)}});
        // 优先文案精确为"+"的，其次 class 含 add/plus 的，再按离标题更近排序
        cands.sort((a,b) => (b.isPlusTxt - a.isPlusTxt) || (b.isPlusIcon - a.isPlusIcon)
                            || (Math.abs(a.dx)+Math.abs(a.dy)) - (Math.abs(b.dx)+Math.abs(b.dy)));
        return JSON.stringify({best: cands[0], all: cands.slice(0, 8)});
    })()
    """
    val = evaluate(tab, js_plus)
    if not val or val == "null":
        print(f"  {MISS} 未找到「+」号按钮（JS 无返回）")
        dump_dom_hint(tab)
        return False
    parsed = json.loads(val)
    if parsed.get("error") == "no-title":
        print(f"  {MISS} 页面上未找到精确文案为「附件管理」的标题元素")
        dump_dom_hint(tab)
        return False
    if parsed.get("error") == "no-candidate":
        tr = parsed.get("titleRect", {})
        print(f"  {MISS} 找到「附件管理」标题 rect={tr}，但其右侧同一行内未找到候选小元素")
        dump_dom_hint(tab)
        return False
    btn = parsed["best"]
    print(f"  {OK} 找到候选「+」按钮 tag={btn.get('tag')} cls={btn.get('cls','')!r} "
          f"txt={btn.get('txt','')!r} size=({btn.get('w')}x{btn.get('h')}) dx={btn.get('dx')} "
          f"center=({btn['x']},{btn['y']})")
    print(f"  {INFO} 同一行候选元素列表: {parsed.get('all')}")

    # 该下拉菜单可能是 :hover 展开（点击会立即切换关闭），先尝试悬浮，
    # 若菜单未出现再退化为点击
    cdp_hover(tab, btn["x"], btn["y"])
    time.sleep(0.6)
    item = (find_by_text(tab, "上传简历", exact=True)
            or find_by_text_excluding(tab, "上传简历", exclude=["制作附件简历"]))
    if not item:
        print(f"  {INFO} 悬浮未触发菜单，尝试点击「+」")
        cdp_click(tab, btn["x"], btn["y"])
        random_delay(0.8, 1.3)
        item = (find_by_text(tab, "上传简历", exact=True)
                or find_by_text_excluding(tab, "上传简历", exclude=["制作附件简历"]))

    sep("STEP 4 — 点击下拉列表里的「上传简历」，等待弹窗")
    if not item:
        print(f"  {MISS} 未找到「上传简历」菜单项（悬浮和点击均未展开菜单）")
        dump_dom_hint(tab)
        return False
    print(f"  {OK} 找到「上传简历」: text={item['txt']!r} center=({item['x']},{item['y']})")
    # 点击前先把鼠标移到目标项上方，保持菜单处于展开态（部分菜单移开鼠标即收起）
    cdp_hover(tab, item["x"], item["y"])
    time.sleep(0.2)
    cdp_click(tab, item["x"], item["y"])
    random_delay(1.0, 1.5)

    sel = wait_popup_visible(tab, [".boss-popup__wrapper", "[class*='popup']", "[class*='modal']", "[class*='dialog']"])
    if not sel:
        print(f"  {MISS} 未检测到「上传简历」弹窗")
        return False
    print(f"  {OK} 弹窗出现: {sel}")
    return True


# ── 步骤 5~6：上传附件简历 → 文件选择器拦截 + 注入测试文件 ───────────────────

class FileChooserCatcher:
    """
    通过 CDP Page.setInterceptFileChooserDialog + Page.fileChooserOpened 事件，
    捕获原生文件选择器弹出时的 backendNodeId，随后用 DOM.setFileInputFiles
    直接把本地文件路径"喂"给 input，完全跳过系统对话框。
    """
    def __init__(self, tab):
        self.tab = tab
        self._event = threading.Event()
        self._backend_node_id = None
        self._raw = None

    def _on_file_chooser_opened(self, **kwargs):
        self._raw = kwargs
        self._backend_node_id = kwargs.get("backendNodeId")
        self._event.set()

    def arm(self):
        self.tab.set_listener("Page.fileChooserOpened", self._on_file_chooser_opened)
        try:
            self.tab.call_method("Page.setInterceptFileChooserDialog", enabled=True)
            return True
        except Exception as e:
            print(f"  {WARN} Page.setInterceptFileChooserDialog 调用失败: {e}")
            return False

    def wait_and_inject(self, file_path: Path, timeout: float = 8.0) -> bool:
        got = self._event.wait(timeout)
        if not got:
            print(f"  {MISS} 未收到 Page.fileChooserOpened 事件（超时 {timeout}s）")
            return self._fallback_set_file_input(file_path)

        print(f"  {OK} 收到 fileChooserOpened 事件: {self._raw}")
        if self._backend_node_id is None:
            print(f"  {WARN} 事件未带 backendNodeId，尝试退化方案")
            return self._fallback_set_file_input(file_path)

        try:
            self.tab.call_method("DOM.setFileInputFiles",
                                 files=[str(file_path)],
                                 backendNodeId=self._backend_node_id)
            print(f"  {OK} DOM.setFileInputFiles 成功（backendNodeId={self._backend_node_id}）"
                  f"  文件={file_path.name}")
            return True
        except Exception as e:
            print(f"  {WARN} DOM.setFileInputFiles(backendNodeId) 失败: {e}，尝试退化方案")
            return self._fallback_set_file_input(file_path)

    def _fallback_set_file_input(self, file_path: Path) -> bool:
        """退化方案：直接用 DOM.querySelector 定位 <input type=file> 拿 nodeId 注入。"""
        try:
            doc = self.tab.call_method("DOM.getDocument", depth=-1, pierce=True)
            root_id = doc["root"]["nodeId"]
            res = self.tab.call_method("DOM.querySelector", nodeId=root_id,
                                       selector="input[type=file]")
            node_id = res.get("nodeId")
            if not node_id:
                print(f"  {MISS} 退化方案：未找到 input[type=file] 节点")
                return False
            self.tab.call_method("DOM.setFileInputFiles",
                                 files=[str(file_path)], nodeId=node_id)
            print(f"  {OK} 退化方案 DOM.setFileInputFiles 成功（nodeId={node_id}）")
            return True
        except Exception as e:
            print(f"  {MISS} 退化方案失败: {e}")
            return False


def step_upload_attachment(tab, file_path: Path) -> bool:
    sep("STEP 5~6 — 点击「上传附件简历」，拦截文件选择器并注入测试文件")
    catcher = FileChooserCatcher(tab)
    if not catcher.arm():
        print(f"  {WARN} 文件选择器拦截未能启用，仍尝试点击观察实际行为")

    item = find_by_text(tab, "上传附件简历")
    if not item:
        # 容错：也可能是「上传附件」「上传简历附件」等措辞
        for alt in ("上传附件", "本地上传", "上传文件"):
            item = find_by_text(tab, alt)
            if item:
                break
    if not item:
        print(f"  {MISS} 未找到「上传附件简历」按钮")
        dump_dom_hint(tab, ".boss-popup__wrapper")
        return False
    print(f"  {OK} 找到按钮: text={item['txt']!r} center=({item['x']},{item['y']})")
    cdp_click(tab, item["x"], item["y"])

    ok = catcher.wait_and_inject(file_path, timeout=8.0)
    if not ok:
        print(f"  {MISS} 文件注入失败，整条链路无法继续")
        return False

    random_delay(1.5, 2.5)
    return True


# ── 步骤 7~8：确认添加 → 关闭提示弹窗 ─────────────────────────────────────────

def step_confirm_add_and_close(tab) -> bool:
    sep("STEP 7 — 等待「确认添加」弹窗，点击确认")
    # 已验证：页面同时存在多个同名 .dialog-wrap（不同弹窗模板常驻 DOM，display:none）。
    # 真正的"附件确认"弹窗有唯一 class upload-preview-dialog，必须精确匹配它，
    # 不能用宽泛的 .dialog-wrap（querySelector 会先命中其它隐藏的同类弹窗）。
    wait_until(tab, "document.body.innerText.includes('附件确认')", timeout=12,
               desc="「附件确认」弹窗出现")
    sel = wait_popup_visible(tab, [".dialog-wrap.upload-preview-dialog",
                                   ".upload-preview-dialog",
                                   ".dialog-wrap.upload-resume-dialog", ".dialog-wrap",
                                   ".boss-popup__wrapper"],
                             timeout=10)
    if not sel:
        print(f"  {MISS} 未检测到「确认添加」弹窗，dump 当前 popup/modal/dialog/mask 类元素：")
        dump = evaluate(tab, """
        (function(){
            const out=[];
            for (const el of document.querySelectorAll('*')) {
                const cls=(el.className||'').toString();
                if(!/popup|modal|dialog|mask|confirm|annex/i.test(cls)) continue;
                const r=el.getBoundingClientRect();
                if(r.width<=0||r.height<=0) continue;
                out.push(el.tagName+' .'+cls.replace(/\\s+/g,'.').slice(0,50)+' "'+(el.innerText||'').slice(0,40).replace(/\\n/g,' ')+'" ('+Math.round(r.left)+','+Math.round(r.top)+' '+Math.round(r.width)+'x'+Math.round(r.height)+')');
            }
            return JSON.stringify({url: window.location.href, items: out.slice(0,25)});
        })()
        """)
        print(f"  {INFO} {dump}")
        return False
    print(f"  {OK} 弹窗出现: {sel}")
    # 已验证：按钮实际文案是「确定添加」（class btn-sure），不是「确认添加」
    if not (click_text(tab, "确定添加", container_sel=sel, exact=True)
            or click_text(tab, "确定添加", container_sel=sel)
            or click_text(tab, "确认添加", container_sel=sel)
            or click_text(tab, "确定", container_sel=sel, exact=True)
            or click_text(tab, "确认", container_sel=sel, exact=True)
            or click_text(tab, "添加", container_sel=sel)):
        cand = evaluate(tab, f"""
        (function(){{
            const root = document.querySelector({json.dumps(sel)});
            if (!root) return JSON.stringify([]);
            const out = [];
            for (const el of root.querySelectorAll('button,a,span,div')) {{
                const txt = (el.innerText||el.textContent||'').trim();
                if (!txt || txt.length > 16) continue;
                if (!/确认|确定|添加|取消|完成|提交|保存|关闭|×|x/i.test(txt)) continue;
                const r = el.getBoundingClientRect();
                if (r.width<=0||r.height<=0) continue;
                out.push({{tag: el.tagName, cls: (el.className||'').toString().slice(0,50),
                           txt, x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}});
            }}
            return JSON.stringify(out.slice(0,15));
        }})()
        """)
        print(f"  {INFO} 候选按钮: {cand}")
        full_txt = evaluate(tab, f"(function(){{const r=document.querySelector({json.dumps(sel)}); return r ? (r.innerText||'').slice(0,500) : '';}})()")
        print(f"  {INFO} 弹窗完整文案: {full_txt!r}")
        all_clickable = evaluate(tab, f"""
        (function(){{
            const root = document.querySelector({json.dumps(sel)});
            if (!root) return JSON.stringify([]);
            const out = [];
            for (const el of root.querySelectorAll('button,a,span,div,i')) {{
                const txt = (el.innerText||el.textContent||'').trim();
                if (el.children.length > 0) continue;
                const r = el.getBoundingClientRect();
                if (r.width<=0||r.height<=0||r.width>250||r.height>80) continue;
                out.push({{tag: el.tagName, cls: (el.className||'').toString().slice(0,40),
                           txt: txt.slice(0,20), x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}});
            }}
            return JSON.stringify(out.slice(0,30));
        }})()
        """)
        print(f"  {INFO} 所有可点击叶子元素: {all_clickable}")
        clicked = False
        try:
            items = json.loads(cand or "[]")
            for it in items:
                if any(k in it["txt"] for k in ("确认", "确定", "添加")):
                    print(f"  {INFO} 尝试点击候选按钮: {it}")
                    cdp_click(tab, it["x"], it["y"])
                    clicked = True
                    break
        except Exception:
            pass
        if not clicked:
            return False
    random_delay(1.0, 1.5)

    sep("STEP 8 — 等待新弹窗，点击右上角「×」关闭")
    # 上一步对话框关闭后页面可能瞬时无新弹窗（直接回到附件管理页），
    # 用更窄的候选 + 短超时探测，找不到就视为无第二弹窗，继续流程
    sel2 = wait_popup_visible(tab, [".boss-popup__wrapper",
                                    ".dialog-wrap.upload-preview-dialog",
                                    "[class*='dialog-wrap'][class*='confirm']",
                                    "[class*='dialog-wrap'][class*='preview']"],
                              timeout=4)
    if not sel2:
        print(f"  {WARN} 未检测到第二个弹窗（可能已自动关闭/无需关闭，继续流程）")
        return True
    print(f"  {OK} 弹窗出现: {sel2}")
    if not click_close_x(tab, sel2):
        print(f"  {WARN} 未找到关闭按钮，假定弹窗会自动消失，继续流程")
        return True
    random_delay(0.8, 1.2)
    return True


def click_close_x(tab, container_sel: str) -> bool:
    """在指定容器内查找右上角的「×」关闭按钮并点击（按文本/图标 class 双重匹配）。"""
    js = f"""
    (function() {{
        const root = document.querySelector({json.dumps(container_sel)});
        if (!root) return null;
        let best = null, bestTop = 1e9;
        const cands = Array.from(root.querySelectorAll('*')).filter(el => {{
            const txt = (el.innerText || el.textContent || '').trim();
            const cls = (el.className || '').toString();
            return (txt === '×' || txt === 'x' || txt === 'X' || txt === '✕'
                    || /close|icon-close|btn-close/i.test(cls)) && el.children.length <= 1;
        }});
        for (const el of cands) {{
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > 60) continue;
            // 右上角：top 最小，且 left 偏右
            if (r.top < bestTop) {{
                bestTop = r.top;
                best = {{x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
                         cls: (el.className||'').toString().slice(0,60)}};
            }}
        }}
        return best ? JSON.stringify(best) : null;
    }})()
    """
    val = evaluate(tab, js)
    if not val or val == "null":
        print(f"  {MISS} 未找到「×」关闭按钮（容器 {container_sel}）")
        dump_dom_hint(tab, container_sel)
        return False
    btn = json.loads(val)
    print(f"  {OK} 找到「×」关闭按钮 cls={btn['cls']!r} center=({btn['x']},{btn['y']})")
    cdp_click(tab, btn["x"], btn["y"])
    return True


# ── 步骤 9~10：回退到聊天页，重新点击该岗位卡片 ───────────────────────────────

def step_back_to_chat_and_reopen(tab, target: dict) -> bool:
    sep("STEP 9 — 回退到聊天页")
    evaluate(tab, "window.history.back()")
    ok = wait_until(tab, "window.location.href.includes('/web/geek/chat')",
                    timeout=10, desc="回退到 /web/geek/chat")
    if not ok:
        print(f"  {MISS} 回退后 URL 未含 /web/geek/chat: {evaluate(tab,'window.location.href')}")
        return False
    wait_until(tab, "document.readyState === 'complete'", timeout=10)
    time.sleep(1.5)
    print(f"  {OK} 已回到聊天页: {evaluate(tab, 'window.location.href')}")

    sep("STEP 10 — 重新点击刚才那个岗位的聊天卡片")
    return click_session_card(tab, target)


def click_session_card(tab, target: dict) -> bool:
    """
    根据之前记录的 {name, company, jobName} 在会话列表中重新定位并点击该卡片。
    （页面通过 window.chat.communicating 暴露 encryptJobId，但会话列表卡片
    本身不直接展示 encryptJobId，因此用 姓名+公司 文本匹配作为定位依据。）
    """
    name    = target.get("name", "")
    company = target.get("companyName", "")
    js = f"""
    (function() {{
        const lis = Array.from(document.querySelectorAll({json.dumps(SESSION_LI)}));
        const wantName = {json.dumps(name)};
        const wantCompany = {json.dumps(company)};
        // 猎头会话卡片显示的是"猎头机构"而非雇主公司名（与 communicating.companyName 不同），
        // 因此优先按"姓名精确匹配"定位；若同名多个会话，再用公司名兜底区分。
        const cands = [];
        for (const li of lis) {{
            const nameTxt = (li.querySelector('.name-text')?.innerText || '').trim();
            if (nameTxt !== wantName) continue;
            const spans = Array.from(li.querySelectorAll('.name-box > span')).map(s => (s.innerText||'').trim());
            const companyTxt = spans[1] || '';
            const r = li.getBoundingClientRect();
            cands.push({{x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
                         name: nameTxt, company: companyTxt,
                         companyMatch: !!wantCompany && companyTxt.includes(wantCompany)}});
        }}
        if (!cands.length) return null;
        const best = cands.find(c => c.companyMatch) || cands[0];
        return JSON.stringify(best);
    }})()
    """
    val = evaluate(tab, js)
    if not val or val == "null":
        print(f"  {MISS} 未在会话列表中找到匹配卡片 (name={name!r}, company={company!r})")
        dump_dom_hint(tab, ".user-list-content")
        return False
    card = json.loads(val)
    print(f"  {OK} 找到卡片: name={card['name']!r} company={card['company']!r} "
          f"center=({card['x']},{card['y']})")
    cdp_click(tab, card["x"], card["y"])
    random_delay(1.5, 2.0)
    return True


# ── 步骤 11：再次发简历 → 关闭弹窗 ────────────────────────────────────────────

def step_send_resume_again_and_close(tab) -> bool:
    sep("STEP 11 — 再次点击「发简历」，弹窗出现后点击「×」关闭")
    if not step_open_send_resume_dialog(tab):
        return False
    return click_close_x(tab, ".boss-popup__wrapper")


# ── 步骤 12~14：右上角「简历」按钮 → 附件管理 → 删除测试附件 ──────────────────

def step_open_resume_panel(tab) -> bool:
    sep("STEP 12 — 点击右上角「简历」按钮，定位「附件管理」框中的测试附件")
    item = find_by_text(tab, "简历", exact=True, max_w=120)
    if not item:
        item = find_by_text(tab, "简历", max_w=120)
    if not item:
        print(f"  {MISS} 未找到右上角「简历」按钮")
        return False
    print(f"  {OK} 点击「简历」按钮: center=({item['x']},{item['y']})")
    cdp_click(tab, item["x"], item["y"])
    random_delay(1.0, 1.5)

    if not wait_until(tab, "document.body.innerText.includes('附件管理')",
                      timeout=8, desc="出现「附件管理」区域"):
        print(f"  {MISS} 未检测到「附件管理」区域")
        return False
    print(f"  {OK} 检测到「附件管理」区域")
    return True


def step_delete_test_attachment(tab) -> bool:
    """
    已验证的真实结构（与「+」菜单同样是 hover 展开模式，不依赖文案"…"）：
      .annex-item                 单个附件行（按文件名匹配 TEST_FILE.stem）
        .annex-item-operate       <a> 触发器，hover 后内部 <ul class="annex-operate-list">
                                  （预览/下载/重命名/编辑/删除）变为可见
          li.annex-operate-delete 删除项（文案"删除"）
    删除确认弹窗：.dialog-wrap.common-dialog.resume-delete，按钮 .btn-sure（文案"确定"）
    """
    sep("STEP 13 — 悬浮测试附件「…」触发器，点击下拉菜单中的「删除」")
    file_stem = TEST_FILE.stem  # 如 test_attachment_20260607_xxxxxx
    js_locate = f"""
    (function() {{
        const stem = {json.dumps(file_stem)};
        const items = Array.from(document.querySelectorAll('.annex-item'));
        const row = items.find(e => (e.innerText||'').includes(stem));
        if (!row) return JSON.stringify({{err: 'no-row'}});
        const rr = row.getBoundingClientRect();
        const op = row.querySelector('.annex-item-operate');
        if (!op) return JSON.stringify({{err: 'no-operate'}});
        const opr = op.getBoundingClientRect();
        return JSON.stringify({{
            row: {{x: Math.round(rr.left+rr.width/2), y: Math.round(rr.top+rr.height/2)}},
            op:  {{x: Math.round(opr.left+opr.width/2), y: Math.round(opr.top+opr.height/2)}},
        }});
    }})()
    """
    raw = evaluate(tab, js_locate)
    if not raw:
        print(f"  {MISS} 定位测试附件行失败（JS 无返回）")
        return False
    info = json.loads(raw)
    if info.get("err"):
        print(f"  {MISS} 未找到含「{file_stem}」的附件行（{info['err']}），可能尚未上传成功")
        dump_dom_hint(tab, container_sel=".annex-list")
        return False
    print(f"  {OK} 定位到测试附件行: row={info['row']} operate={info['op']}")

    # hover 行 → hover 操作触发器，使下拉菜单(ul.annex-operate-list)可见
    cdp_hover(tab, info["row"]["x"], info["row"]["y"])
    time.sleep(0.4)
    cdp_hover(tab, info["op"]["x"], info["op"]["y"])
    time.sleep(0.6)

    js_del_pos = f"""
    (function() {{
        const stem = {json.dumps(file_stem)};
        const items = Array.from(document.querySelectorAll('.annex-item'));
        const row = items.find(e => (e.innerText||'').includes(stem));
        if (!row) return null;
        const del = row.querySelector('.annex-operate-delete');
        if (!del) return null;
        const r = del.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        return JSON.stringify({{x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}});
    }})()
    """
    pos_raw = evaluate(tab, js_del_pos)
    if not pos_raw or pos_raw == "null":
        print(f"  {MISS} 「删除」菜单项未出现（hover 未触发下拉菜单展开）")
        return False
    pos = json.loads(pos_raw)
    print(f"  {OK} 找到「删除」菜单项 center=({pos['x']},{pos['y']})")
    cdp_hover(tab, pos["x"], pos["y"])
    time.sleep(0.2)
    cdp_click(tab, pos["x"], pos["y"])
    random_delay(1.0, 1.5)

    sep("STEP 14 — 等待删除确认弹窗，点击「确定」")
    sel = wait_popup_visible(tab, [".dialog-wrap.common-dialog.resume-delete",
                                   ".dialog-wrap.resume-delete",
                                   ".boss-popup__wrapper"],
                             timeout=8)
    if not sel:
        print(f"  {MISS} 未检测到删除确认弹窗")
        return False
    print(f"  {OK} 弹窗出现: {sel}")
    if not (click_text(tab, "确定", container_sel=sel, exact=True)
            or click_text(tab, "确定", container_sel=sel)):
        return False
    random_delay(1.0, 1.5)

    # 验证删除结果：附件列表中不应再含 file_stem
    still_there = evaluate(tab, f"""
        Array.from(document.querySelectorAll('.annex-item'))
             .some(e => (e.innerText||'').includes({json.dumps(file_stem)}))
    """)
    if still_there:
        print(f"  {WARN} 删除后附件列表中仍可见该文件，可能删除未生效")
    else:
        print(f"  {OK} 已确认测试附件从列表中移除")
    return True


# ── 步骤 15：回退聊天页，重新点击该岗位卡片，结束 ─────────────────────────────

def step_finish(tab, target: dict) -> bool:
    sep("STEP 15 — 回退到聊天页，重新点击该岗位卡片，结束流程")
    evaluate(tab, "window.history.back()")
    ok = wait_until(tab, "window.location.href.includes('/web/geek/chat')",
                    timeout=10, desc="回退到 /web/geek/chat")
    if not ok:
        # 可能根本没有发生跳转（简历面板是同页弹层），直接判断是否已在聊天页
        if "/web/geek/chat" not in (evaluate(tab, "window.location.href") or ""):
            print(f"  {MISS} 未回到聊天页: {evaluate(tab,'window.location.href')}")
            return False
    wait_until(tab, "document.readyState === 'complete'", timeout=10)
    time.sleep(1.0)
    return click_session_card(tab, target)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    sep("「发简历→管理附件→上传/删除附件」全流程测试")
    print(f"  {INFO} 测试附件标记: {TEST_MARK}")
    print(f"  {INFO} 测试文件路径: {TEST_FILE}")

    chat_meta = find_chat_tab()
    if not chat_meta:
        print(f"  {MISS} 未找到聊天页标签（请确认已打开 /web/geek/chat 并登录）")
        return
    print(f"  {OK} 聊天页标签: id={chat_meta['id']}  url={chat_meta.get('url','')[:80]}")

    browser = pychrome.Browser(url=CDP_URL)
    tab = next(t for t in browser.list_tab() if t.id == chat_meta["id"])
    tab.start()
    try:
        tab.call_method("Page.enable")
        tab.call_method("DOM.enable")
    except Exception:
        pass

    try:
        _CHAT_INFO_JS = """
        (function(){
            const c = window.chat && window.chat.communicating;
            return c && c.encryptJobId ? JSON.stringify({name: c.name||'', companyName: c.companyName||'',
                                        jobName: c.jobName||'', encryptJobId: c.encryptJobId||''}) : 'null';
        })()
        """
        chat_info = json.loads(evaluate(tab, _CHAT_INFO_JS) or "null")
        if not chat_info:
            print(f"  {INFO} 当前未打开任何会话，尝试依次点击会话列表项直到找到含 encryptJobId 的会话...")
            for idx in range(8):
                pos_raw = evaluate(tab, f"""
                (function(){{
                    const lis = document.querySelectorAll({json.dumps(SESSION_LI)});
                    const li = lis[{idx}];
                    if (!li) return null;
                    li.scrollIntoView({{block:'center', behavior:'instant'}});
                    const r = li.getBoundingClientRect();
                    return JSON.stringify({{x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}});
                }})()
                """)
                if not pos_raw or pos_raw == "null":
                    break
                pos = json.loads(pos_raw)
                cdp_click(tab, pos["x"], pos["y"])
                for _ in range(6):
                    random_delay(0.8, 1.2)
                    chat_info = json.loads(evaluate(tab, _CHAT_INFO_JS) or "null")
                    if chat_info:
                        break
                if chat_info:
                    print(f"  {INFO} 第 {idx+1} 个会话项可用")
                    break
        if not chat_info:
            print(f"  {MISS} 未读到 window.chat.communicating，请先在页面打开一个会话")
            return
        print(f"  {INFO} 当前会话: name={chat_info['name']!r} company={chat_info['companyName']!r} "
              f"jobName={chat_info['jobName']!r} encryptJobId={chat_info['encryptJobId']!r}")

        _write_minimal_pdf(TEST_FILE, TEST_MARK)
        print(f"  {OK} 已生成测试文件: {TEST_FILE} ({TEST_FILE.stat().st_size} bytes)")

        steps = [
            ("发简历→管理附件",     lambda: step_open_send_resume_dialog(tab) and step_click_manage_attachment(tab)),
            ("+ → 上传简历",        lambda: step_click_plus_and_upload_resume(tab)),
            ("上传附件简历→注入文件", lambda: step_upload_attachment(tab, TEST_FILE)),
            ("确认添加→关闭弹窗",    lambda: step_confirm_add_and_close(tab)),
            ("回退→重新打开会话",    lambda: step_back_to_chat_and_reopen(tab, chat_info)),
            ("再次发简历→关闭",      lambda: step_send_resume_again_and_close(tab)),
            ("打开简历面板",        lambda: step_open_resume_panel(tab)),
            ("删除测试附件",        lambda: step_delete_test_attachment(tab)),
            ("回退→重新打开会话(结束)", lambda: step_finish(tab, chat_info)),
        ]

        for label, fn in steps:
            ok = fn()
            if not ok:
                sep(f"✗ 流程在「{label}」步骤失败，已终止")
                print(f"  {INFO} 测试文件保留在: {TEST_FILE}（如已上传成功但删除失败，请手动到附件管理中清理）")
                return
            random_delay(0.8, 1.3)

        sep("✓ 全部步骤完成")

    finally:
        try:
            tab.call_method("Page.setInterceptFileChooserDialog", enabled=False)
        except Exception:
            pass
        tab.stop()


if __name__ == "__main__":
    main()

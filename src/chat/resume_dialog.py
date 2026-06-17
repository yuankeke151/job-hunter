import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.cdp_utils import evaluate, cdp_click, random_delay
from shared.logger import log


def _handle_resume_confirm_popover(tab) -> bool:
    """
    处理「单附件」场景下出现的非模态确认气泡（点击其他地方仍可交互）。

    探索发现的结构（与 .boss-popup__wrapper 多附件弹窗完全不同，不含 popup/dialog/modal 类名，
    非排他、不带遮罩，是锚定在工具栏附近的小气泡）：
      容器     : .panel-resume.sentence-popover  （360×125，标题"确定向 Boss 发送简历吗？"）
      取消按钮 : span.btn-v2.btn-outline-v2  text='取消'
      确认按钮 : span.btn-v2.btn-sure-v2     text='确定'
    """
    log.info("  [简历气泡] 检测到单附件确认气泡 (.panel-resume.sentence-popover)，点击「确定」")
    js_confirm = """
    (function() {
        const popover = document.querySelector('.panel-resume.sentence-popover');
        if (!popover) return null;
        for (const b of popover.querySelectorAll('span.btn-v2, .btn-v2')) {
            const txt = (b.innerText || '').trim();
            if (txt !== '确定') continue;
            const r = b.getBoundingClientRect();
            if (r.width > 0 && r.height > 0)
                return JSON.stringify({
                    x: Math.round(r.left + r.width/2),
                    y: Math.round(r.top  + r.height/2),
                    txt, cls: (b.className||'').trim().slice(0, 60),
                });
        }
        return null;
    })()
    """
    val = evaluate(tab, js_confirm)
    if not val or val == "null":
        log.warning("  [简历气泡] ✗ 未找到「确定」按钮")
        return False
    try:
        btn = json.loads(val)
        log.info(f"  [简历气泡] ✓ 点击确认: {btn['txt']!r}  cls={btn['cls']!r}  "
                 f"center=({btn['x']},{btn['y']})")
        cdp_click(tab, btn["x"], btn["y"])
        random_delay(1.5, 2.5)
        return True
    except Exception as e:
        log.error(f"  [简历气泡] 点击确认失败: {e}")
        return False


def handle_resume_dialog(tab, resume_name_match: str = "袁柯.pdf") -> bool:
    """
    点击「发简历」后会出现两种互斥的提示之一（取决于简历附件数量，调用方无需预判）：
      A. 附件数 > 1：弹出排他的简历选择模态框 .boss-popup__wrapper
         → 在 span.resume-name 中选中 resume_name_match 对应的简历项，点击「发送」确认
      B. 附件数 == 1：弹出非模态、可穿透点击的确认气泡 .panel-resume.sentence-popover
         （标题"确定向 Boss 发送简历吗？"），无需选择简历项，直接点击「确定」即可
    本函数轮询检测两种弹层中先出现的那个并分别处理。

    已验证的选择器（debug_chat5.py + 实机探索）：
      模态框   : .boss-popup__wrapper  (z=2014, 580×318)
      简历项   : span.resume-name  含「袁柯」
      选中态   : [class*="select-one"] 出现
      确认按钮 : .btn-confirm  text='发送'
      气泡     : .panel-resume.sentence-popover （非模态，确认按钮 span.btn-v2.btn-sure-v2 text='确定'）
    """
    log.info("  [简历弹窗] 等待弹窗/气泡出现...")

    js_check = """
    (function() {
        function vis(sel) {
            const el = document.querySelector(sel);
            if (!el) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
        }
        if (vis('.boss-popup__wrapper')) return 'modal';
        if (vis('.panel-resume.sentence-popover')) return 'popover';
        return null;
    })()
    """
    kind = None
    for _ in range(10):
        time.sleep(0.5)
        kind = evaluate(tab, js_check)
        if kind in ("modal", "popover"):
            break
    else:
        log.warning("  [简历弹窗] 等待超时，弹窗/气泡均未出现")
        return False

    if kind == "popover":
        log.info("  [简历弹窗] 检测到单附件确认气泡 (.panel-resume.sentence-popover)")
        return _handle_resume_confirm_popover(tab)

    log.info("  [简历弹窗] 检测到弹窗 (.boss-popup__wrapper)")
    time.sleep(0.5)

    want_json = json.dumps(resume_name_match)
    js_find = """
    (function() {
        const want = """ + want_json + """;
        for (const el of document.querySelectorAll('span.resume-name')) {
            const txt = (el.innerText || '').trim();
            if (!txt.includes(want)) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            return JSON.stringify({
                x: Math.round(r.left + r.width/2),
                y: Math.round(r.top  + r.height/2),
                txt: txt.slice(0, 40),
                cls: (el.className||'').trim().slice(0, 60),
            });
        }
        const popup = document.querySelector('.boss-popup__wrapper');
        if (!popup) return null;
        let best = null, bestScore = -99;
        popup.querySelectorAll('*').forEach(el => {
            const txt = (el.innerText || '').trim();
            if (!txt.includes(want)) return;
            const r = el.getBoundingClientRect();
            if (r.width < 20 || r.height < 8 || r.width > 600) return;
            const cls = (el.className||'').toString();
            const score = (cls.includes('resume') ? 10 : 0)
                        + (el.children.length === 0 ? 5 : 0)
                        - el.children.length * 2;
            if (score > bestScore) {
                bestScore = score;
                best = { x: Math.round(r.left+r.width/2),
                         y: Math.round(r.top+r.height/2),
                         txt: txt.slice(0,40),
                         cls: cls.replace(/\\s+/g,' ').trim().slice(0,60) };
            }
        });
        return best ? JSON.stringify(best) : null;
    })()
    """
    val = evaluate(tab, js_find)
    if not val or val == "null":
        log.warning(f"  [简历弹窗] ✗ 未找到「{resume_name_match}」简历项")
        return False

    try:
        item = json.loads(val)
        log.info(f"  [简历弹窗] ✓ 点击简历: {item['txt']!r}  "
                 f"cls={item['cls']!r}  center=({item['x']},{item['y']})")
        cdp_click(tab, item["x"], item["y"])
        time.sleep(0.8)
    except Exception as e:
        log.error(f"  [简历弹窗] 点击简历项失败: {e}")
        return False

    js_confirm = """
    (function() {
        let btn = document.querySelector('.btn-confirm');
        if (btn) {
            const r = btn.getBoundingClientRect();
            if (r.width > 0)
                return JSON.stringify({
                    x: Math.round(r.left+r.width/2),
                    y: Math.round(r.top+r.height/2),
                    txt: (btn.innerText||'').trim(),
                    cls: (btn.className||'').trim().slice(0,60),
                });
        }
        const popup = document.querySelector('.boss-popup__wrapper');
        if (!popup) return null;
        for (const b of popup.querySelectorAll('button, .btn, .btn-v2')) {
            const txt = (b.innerText||'').trim();
            const r   = b.getBoundingClientRect();
            if (txt === '发送' && r.width > 0)
                return JSON.stringify({
                    x: Math.round(r.left+r.width/2),
                    y: Math.round(r.top+r.height/2),
                    txt, cls: (b.className||'').trim().slice(0,60),
                });
        }
        return null;
    })()
    """
    time.sleep(0.3)

    val2 = evaluate(tab, js_confirm)
    if not val2 or val2 == "null":
        log.warning("  [简历弹窗] ✗ 未找到「发送」确认按钮")
        return False

    try:
        btn = json.loads(val2)
        log.info(f"  [简历弹窗] ✓ 点击确认: {btn['txt']!r}  cls={btn['cls']!r}  "
                 f"center=({btn['x']},{btn['y']})")
        cdp_click(tab, btn["x"], btn["y"])
        random_delay(1.5, 2.5)
        return True
    except Exception as e:
        log.error(f"  [简历弹窗] 点击确认失败: {e}")
        return False


def click_resume_btn(tab, resume_name_match: str = "袁柯.pdf") -> bool:
    """直接点击工具栏「发简历」按钮并处理弹窗（无消息数量前提条件）。"""
    js_btn = """
    (function() {
        for (const el of document.querySelectorAll('.toolbar-btn-content')) {
            const txt = (el.innerText || '').trim();
            if (txt === '发简历' || txt.includes('发简历')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    return JSON.stringify({
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top  + r.height/2),
                    });
            }
        }
        return null;
    })()
    """
    val = evaluate(tab, js_btn)
    if not val or val == "null":
        log.warning("  [发简历] 未找到工具栏按钮")
        return False
    btn = json.loads(val)
    log.info(f"  [发简历] 点击按钮 center=({btn['x']},{btn['y']})")
    cdp_click(tab, btn["x"], btn["y"])
    random_delay(1.0, 1.5)
    return handle_resume_dialog(tab, resume_name_match=resume_name_match)

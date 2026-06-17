import sys, json, time, threading
from pathlib import Path
import pychrome
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import GENERATE_TAILORED_RESUME
from shared.cdp_utils import (evaluate, cdp_click, cdp_wheel, random_delay,
                              scroll_into_view_and_click, SESSION_LI)
from shared.logger import log
from chat.resume_dialog import click_resume_btn

# ── 定制简历：上传 / 删除附件（GENERATE_TAILORED_RESUME=True 时使用）──────────
# 选择器移植自已验证的 src/debug/debug_resume_attachment_flow.py
# （SESSION_LI 已迁移至 shared.cdp_utils，与 handler.py 共用，避免重复定义）


def cdp_hover(tab, x: float, y: float):
    """发送 mouseMoved 事件触发 hover 态（部分下拉菜单是 :hover 展开而非 click 切换）。"""
    tab.call_method("Input.dispatchMouseEvent", type="mouseMoved",
                    x=x, y=y, button="none", modifiers=0)


def _wait_until(tab, js_cond: str, timeout: float = 8.0, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if evaluate(tab, js_cond):
            return True
        time.sleep(interval)
    return False


def _wait_popup_visible(tab, selectors: list[str], timeout: float = 8.0) -> str | None:
    js = f"""
    (function() {{
        const sels = {json.dumps(selectors)};
        for (const sel of sels) {{
            for (const el of document.querySelectorAll(sel)) {{
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


def _find_by_text(tab, text: str, container_sel: str | None = None,
                  exact: bool = False, max_w: int = 700) -> dict | None:
    root_expr = (f"document.querySelector({json.dumps(container_sel)})"
                 if container_sel else "document.body")
    cmp_expr = (f"txt === {json.dumps(text)}" if exact
                else f"txt.includes({json.dumps(text)})")
    js = f"""
    (function() {{
        const root = {root_expr};
        if (!root) return null;
        let best = null, bestScore = -999;
        for (const el of root.querySelectorAll('*')) {{
            const txt = (el.innerText || el.textContent || '').trim();
            if (!txt || txt.length > 60) continue;
            if (!({cmp_expr})) continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > {max_w}) continue;
            const exactBonus = (txt === {json.dumps(text)}) ? 100 : 0;
            const lenPenalty = Math.abs(txt.length - {len(text)});
            const score = exactBonus + (el.children.length === 0 ? 20 : 0)
                        - el.children.length * 3 - lenPenalty * 4;
            if (score > bestScore) {{
                bestScore = score;
                best = {{x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
                         txt: txt.slice(0, 40),
                         cls: (el.className||'').toString().replace(/\\s+/g,' ').trim().slice(0, 80)}};
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


def _click_text(tab, text: str, container_sel: str | None = None, exact: bool = False) -> bool:
    item = _find_by_text(tab, text, container_sel=container_sel, exact=exact)
    if not item:
        return False
    cdp_click(tab, item["x"], item["y"])
    return True


def _click_close_x(tab, container_sel: str) -> bool:
    js = f"""
    (function() {{
        const root = document.querySelector({json.dumps(container_sel)});
        if (!root) return null;
        let best = null, bestTop = 1e9;
        for (const el of root.querySelectorAll('*')) {{
            const txt = (el.innerText || el.textContent || '').trim();
            const cls = (el.className || '').toString();
            if (!((txt === '×' || txt === 'x' || txt === 'X' || txt === '✕'
                   || /close|icon-close|btn-close/i.test(cls)) && el.children.length <= 1)) continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > 60) continue;
            if (r.top < bestTop) {{
                bestTop = r.top;
                best = {{x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}};
            }}
        }}
        return best ? JSON.stringify(best) : null;
    }})()
    """
    val = evaluate(tab, js)
    if not val or val == "null":
        return False
    btn = json.loads(val)
    cdp_click(tab, btn["x"], btn["y"])
    return True


def click_session_card(tab, target: dict) -> bool:
    """按 {name, companyName} 在会话列表中重新定位并点击该卡片（猎头卡片显示机构名而非雇主名，姓名优先匹配）。
    防视口外点击失效：先定位目标 li 并 scrollIntoView 滚动进视口中央，等待重排后再重新取最新坐标点击
    （与 CLAUDE.md「左侧会话卡片点击」一节、scanner.py 点击职位卡片同款防护模式一致）。"""
    name    = target.get("name", "")
    company = target.get("companyName", "")

    # 定位表达式：按姓名精确匹配、公司名包含匹配优先，返回最佳候选 li（或 null）。
    # 同时供下方"查找+滚动"循环（包一层 scrollIntoView）和最终点击（scroll_into_view_and_click）复用。
    locate_li_js = f"""
        const lis = Array.from(document.querySelectorAll({json.dumps(SESSION_LI)}));
        const wantName = {json.dumps(name)};
        const wantCompany = {json.dumps(company)};
        const cands = [];
        for (const li of lis) {{
            const nameTxt = (li.querySelector('.name-text')?.innerText || '').trim();
            if (nameTxt !== wantName) continue;
            const spans = Array.from(li.querySelectorAll('.name-box > span')).map(s => (s.innerText||'').trim());
            const companyTxt = spans[1] || '';
            cands.push({{ li, companyMatch: !!wantCompany && companyTxt.includes(wantCompany) }});
        }}
        if (!cands.length) return null;
        const best = cands.find(c => c.companyMatch) || cands[0];
        return best.li;
    """

    def _found_in_view() -> bool:
        return bool(evaluate(tab, f"""
        (function() {{
            {locate_li_js}
        }})()
        """.replace("return best.li;",
                    "best.li.scrollIntoView({block:'center', behavior:'instant'}); return true;")))

    # 会话列表是懒加载/虚拟滚动的，回退后列表会重置到顶部——目标卡片可能尚未渲染进 DOM，
    # 直接查询会得到 null。需要像 scanner 翻页一样，边向下滚动列表容器边重试匹配，
    # 直到找到目标卡片，或连续多轮容器内卡片数都不再变化（判定已到底部，目标确实不存在）。
    list_count_js = f"document.querySelectorAll({json.dumps(SESSION_LI)}).length"
    container_rect_js = """
    (function() {
        const el = document.querySelector('.user-list-content');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return JSON.stringify({x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)});
    })()
    """
    found = False
    stale = 0
    last_count = -1
    for _ in range(40):
        found = _found_in_view()
        if found:
            break
        cur_count = evaluate(tab, list_count_js) or 0
        if cur_count == last_count:
            stale += 1
            if stale >= 2:
                break
        else:
            stale = 0
            last_count = cur_count
        pos_raw = evaluate(tab, container_rect_js)
        if not pos_raw or pos_raw == "null":
            break
        pos = json.loads(pos_raw)
        cdp_wheel(tab, pos["x"], pos["y"], 600)
        evaluate(tab, "document.querySelector('.user-list-content')?.scrollBy(0, 600)")
        time.sleep(0.6)

    if not found:
        log.warning(f"  [定制简历] 未在会话列表中找到匹配卡片 (name={name!r}, company={company!r})")
        return False

    time.sleep(0.4)  # 等待滚动 + 重排完成

    # 第二步：重新定位同一张卡片，scrollIntoView 滚入视口中央并取此刻的最新坐标点击
    # （与 handler.py 左侧会话卡片点击同款防视口外失效模式，已提取为公共助手）
    if not scroll_into_view_and_click(tab, locate_li_js, delay=(1.5, 2.0)):
        log.warning(f"  [定制简历] 滚动后重新定位卡片失败 (name={name!r}, company={company!r})")
        return False
    return True


def upload_resume_attachment(tab, pdf_path: Path, target: dict) -> bool:
    """
    点击右上角「简历」面板 →「附件管理」区域出现（与 delete_resume_attachment 前两步一致）
    →「+」→「上传简历」→「上传附件简历」→ 拦截文件选择器注入 pdf_path
    → 「确定添加」→ 关闭提示弹窗 → 回退到聊天页并重新点击会话卡片。
    流程移植自 debug_resume_attachment_flow.py（已验证选择器）。
    """
    log.info(f"  [定制简历-上传] 开始上传附件: {pdf_path.name}")

    # 点击右上角「简历」，页面应跳转到 /web/geek/resume，最多重试 5 次
    item = _find_by_text(tab, "简历", exact=True, max_w=120) or _find_by_text(tab, "简历", max_w=120)
    if not item:
        log.warning("  [定制简历-上传] 未找到右上角「简历」按钮")
        return False
    url_before = evaluate(tab, "window.location.href") or ""
    for retry in range(5):
        cdp_click(tab, item["x"], item["y"])
        random_delay(2.0, 3.0)
        url_after = evaluate(tab, "window.location.href") or ""
        if url_after != url_before and "/web/geek/resume" in url_after:
            log.info(f"  [定制简历-上传] 已跳转到简历管理页 (retry={retry})")
            break
        log.warning(f"  [定制简历-上传] 未跳转 (retry={retry+1}/5)，重试...")
    else:
        log.warning("  [定制简历-上传] 5 次重试后仍未跳转，跳过该对话")
        return False
    if not _wait_until(tab, "document.body.innerText.includes('附件管理')", timeout=8):
        log.warning("  [定制简历-上传] 未检测到「附件管理」区域")
        return False

    # 附件数量已满检测：.resume-type-title 文案形如「文件（2/3）」，current>=max 时平台不允许再上传，
    # 直接判定本次上传失败、跳过该对话
    count_raw = evaluate(tab, """
    (function() {
        const el = document.querySelector('.resume-type-title');
        if (!el) return null;
        const m = (el.innerText || '').match(/(\\d+)\\s*[/／]\\s*(\\d+)/);
        if (!m) return null;
        return JSON.stringify({cur: parseInt(m[1], 10), max: parseInt(m[2], 10), txt: el.innerText.trim()});
    })()
    """)
    if count_raw and count_raw != "null":
        try:
            info = json.loads(count_raw)
            log.info(f"  [定制简历-上传] 附件数量: {info['txt']}")
            if info["cur"] >= info["max"]:
                log.warning(f"  [定制简历-上传] 附件已达上限（{info['txt']}），无法上传新附件，跳过该对话")
                evaluate(tab, "window.history.back()")
                _wait_until(tab, "window.location.href.includes('/web/geek/chat')", timeout=10)
                _wait_until(tab, "document.readyState === 'complete'", timeout=10)
                time.sleep(1.5)
                click_session_card(tab, target)
                return False
        except Exception as e:
            log.warning(f"  [定制简历-上传] 解析附件数量失败: {e}")

    # 「+」→「上传简历」
    js_plus = """
    (function() {
        const el = document.querySelector('.resume-attachment a.sider-title-operate')
                || document.querySelector('a.sider-title-operate');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        return JSON.stringify({x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)});
    })()
    """
    val = evaluate(tab, js_plus)
    if not val or val == "null":
        log.warning("  [定制简历-上传] 未找到「+」按钮")
        return False
    plus = json.loads(val)
    cdp_hover(tab, plus["x"], plus["y"])
    time.sleep(0.6)
    item = (_find_by_text(tab, "上传简历", exact=True))
    if not item:
        cdp_click(tab, plus["x"], plus["y"])
        random_delay(0.8, 1.3)
        item = _find_by_text(tab, "上传简历", exact=True)
    if not item:
        log.warning("  [定制简历-上传] 未找到「上传简历」菜单项")
        return False
    cdp_hover(tab, item["x"], item["y"])
    time.sleep(0.2)
    cdp_click(tab, item["x"], item["y"])
    random_delay(1.0, 1.5)
    if not _wait_popup_visible(tab, [".boss-popup__wrapper", "[class*='dialog']"]):
        log.warning("  [定制简历-上传] 「上传简历」弹窗未出现")
        return False

    # 「上传附件简历」→ 拦截文件选择器并注入
    # 先定位按钮坐标，紧贴点击前再 arm 拦截，缩短"拦截生效"与"触发选择器"之间的时间窗口
    item = _find_by_text(tab, "上传附件简历") or _find_by_text(tab, "上传附件")
    if not item:
        log.warning("  [定制简历-上传] 未找到「上传附件简历」按钮")
        return False
    catcher = _FileChooserCatcher(tab)
    catcher.arm()
    cdp_click(tab, item["x"], item["y"])
    try:
        if not catcher.wait_and_inject(pdf_path, timeout=8.0):
            log.warning("  [定制简历-上传] 文件注入失败")
            return False
    finally:
        catcher.disarm()
    random_delay(1.5, 2.5)

    # 「确定添加」
    if not _wait_until(tab, "document.body.innerText.includes('附件确认')", timeout=12):
        log.warning("  [定制简历-上传] 「附件确认」弹窗未出现")
    sel = _wait_popup_visible(tab, [".dialog-wrap.upload-preview-dialog", ".dialog-wrap"], timeout=10)
    if not sel:
        log.warning("  [定制简历-上传] 「确认添加」弹窗未出现")
        return False
    if not (_click_text(tab, "确定添加", container_sel=sel)
            or _click_text(tab, "确定", container_sel=sel, exact=True)
            or _click_text(tab, "添加", container_sel=sel)):
        log.warning("  [定制简历-上传] 未找到「确定添加」按钮")
        return False
    random_delay(2.0, 2.5)

    # 可能出现的提示弹窗，关闭即可（不影响主流程）
    sel2 = _wait_popup_visible(tab, [".boss-popup__wrapper", ".dialog-wrap.upload-preview-dialog"], timeout=4)
    if sel2:
        _click_close_x(tab, sel2)
        random_delay(0.8, 1.2)

    # 回退到聊天页，重新打开会话
    evaluate(tab, "window.history.back()")
    if not _wait_until(tab, "window.location.href.includes('/web/geek/chat')", timeout=10):
        log.warning("  [定制简历-上传] 回退到聊天页失败")
        return False
    _wait_until(tab, "document.readyState === 'complete'", timeout=10)
    time.sleep(1.5)
    if not click_session_card(tab, target):
        return False

    log.info("  [定制简历-上传] ✓ 上传完成")
    return True


def delete_resume_attachment(tab, name_match: str, target: dict) -> bool:
    """
    点击右上角「简历」面板 → 在「附件管理」中找到 name_match 对应附件行 → hover 展开操作菜单 →
    点击「删除」→ 确认弹窗点击「确定」→ 回退到聊天页并重新点击会话卡片。
    流程移植自 debug_resume_attachment_flow.py（已验证选择器）。
    """
    log.info(f"  [定制简历-删除] 开始删除附件: {name_match}")

    # 点击右上角「简历」，页面应跳转到 /web/geek/resume，最多重试 5 次
    item = _find_by_text(tab, "简历", exact=True, max_w=120) or _find_by_text(tab, "简历", max_w=120)
    if not item:
        log.warning("  [定制简历-删除] 未找到右上角「简历」按钮")
        return False
    url_before = evaluate(tab, "window.location.href") or ""
    for retry in range(5):
        cdp_click(tab, item["x"], item["y"])
        random_delay(2.0, 3.0)
        url_after = evaluate(tab, "window.location.href") or ""
        if url_after != url_before and "/web/geek/resume" in url_after:
            log.info(f"  [定制简历-删除] 已跳转到简历管理页 (retry={retry})")
            break
        log.warning(f"  [定制简历-删除] 未跳转 (retry={retry+1}/5)，重试...")
    else:
        log.warning("  [定制简历-删除] 5 次重试后仍未跳转，跳过该对话")
        return False
    if not _wait_until(tab, "document.body.innerText.includes('附件管理')", timeout=8):
        log.warning("  [定制简历-删除] 未检测到「附件管理」区域")
        return False

    js_locate = f"""
    (function() {{
        const stem = {json.dumps(name_match)};
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
    info = json.loads(raw) if raw else {"err": "no-data"}
    if info.get("err"):
        log.warning(f"  [定制简历-删除] 未找到含「{name_match}」的附件行 ({info['err']})")
        return False

    cdp_hover(tab, info["row"]["x"], info["row"]["y"])
    time.sleep(0.4)
    cdp_hover(tab, info["op"]["x"], info["op"]["y"])
    time.sleep(0.6)

    js_del_pos = f"""
    (function() {{
        const stem = {json.dumps(name_match)};
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
        log.warning("  [定制简历-删除] 「删除」菜单项未出现")
        return False
    pos = json.loads(pos_raw)
    cdp_hover(tab, pos["x"], pos["y"])
    time.sleep(0.2)
    cdp_click(tab, pos["x"], pos["y"])
    random_delay(1.0, 1.5)

    sel = _wait_popup_visible(tab, [".dialog-wrap.common-dialog.resume-delete",
                                    ".dialog-wrap.resume-delete", ".boss-popup__wrapper"], timeout=8)
    if not sel:
        log.warning("  [定制简历-删除] 未检测到删除确认弹窗")
        return False
    if not (_click_text(tab, "确定", container_sel=sel, exact=True)
            or _click_text(tab, "确定", container_sel=sel)):
        log.warning("  [定制简历-删除] 未找到「确定」按钮")
        return False
    random_delay(1.0, 1.5)

    # 回退到聊天页，重新打开会话
    evaluate(tab, "window.history.back()")
    if not _wait_until(tab, "window.location.href.includes('/web/geek/chat')", timeout=10):
        if "/web/geek/chat" not in (evaluate(tab, "window.location.href") or ""):
            log.warning("  [定制简历-删除] 未回到聊天页")
            return False
    _wait_until(tab, "document.readyState === 'complete'", timeout=10)
    time.sleep(1.0)
    click_session_card(tab, target)

    log.info("  [定制简历-删除] ✓ 删除完成")
    return True


class _FileChooserCatcher:
    """通过 Page.setInterceptFileChooserDialog + Page.fileChooserOpened 捕获文件选择器并注入本地文件。
    移植自 debug_resume_attachment_flow.py 的 FileChooserCatcher。"""
    def __init__(self, tab):
        self.tab = tab
        self._event = threading.Event()
        self._backend_node_id = None

    def _on_opened(self, **kwargs):
        self._backend_node_id = kwargs.get("backendNodeId")
        self._event.set()

    def arm(self) -> bool:
        # Page 域必须先启用，setInterceptFileChooserDialog 才能真正生效
        # （重复调用 Page.enable 是幂等的，不会因主流程已启用而出错）
        try:
            self.tab.call_method("Page.enable")
        except Exception as e:
            log.warning(f"  [定制简历] Page.enable 失败: {e}")
        self.tab.set_listener("Page.fileChooserOpened", self._on_opened)
        try:
            self.tab.call_method("Page.setInterceptFileChooserDialog", enabled=True)
            log.info("  [定制简历-上传] 文件选择器拦截已启用")
            return True
        except Exception as e:
            log.warning(f"  [定制简历] setInterceptFileChooserDialog 失败: {e}")
            return False

    def disarm(self):
        try:
            self.tab.call_method("Page.setInterceptFileChooserDialog", enabled=False)
        except Exception:
            pass

    def wait_and_inject(self, file_path: Path, timeout: float = 8.0) -> bool:
        # 优先尝试事件回调方式（拦截已生效时，原生选择器不会真正弹出）
        if self._event.wait(timeout) and self._backend_node_id is not None:
            try:
                self.tab.call_method("DOM.setFileInputFiles", files=[str(file_path)],
                                     backendNodeId=self._backend_node_id)
                log.info(f"  [定制简历-上传] 通过 fileChooserOpened 注入成功 "
                         f"(backendNodeId={self._backend_node_id})")
                return True
            except Exception as e:
                log.warning(f"  [定制简历-上传] backendNodeId 注入失败: {e}，尝试直接定位 input")
        else:
            log.warning("  [定制简历-上传] 未收到 fileChooserOpened 事件"
                        "（拦截可能未生效，原生选择器可能已弹出），尝试直接定位 input[type=file] 注入")
        # 直接定位 <input type=file> 注入：不依赖事件回调，规避拦截未及时生效的时序问题
        return self._direct_set_file_input(file_path)

    def _direct_set_file_input(self, file_path: Path) -> bool:
        try:
            doc = self.tab.call_method("DOM.getDocument", depth=-1, pierce=True)
            root_id = doc["root"]["nodeId"]
            res = self.tab.call_method("DOM.querySelector", nodeId=root_id, selector="input[type=file]")
            node_id = res.get("nodeId")
            if not node_id:
                log.warning("  [定制简历-上传] 未找到 input[type=file] 节点")
                return False
            self.tab.call_method("DOM.setFileInputFiles", files=[str(file_path)], nodeId=node_id)
            log.info(f"  [定制简历-上传] 直接定位 input[type=file] 注入成功 (nodeId={node_id})")
            return True
        except Exception as e:
            log.warning(f"  [定制简历-上传] 直接定位 input 注入失败: {e}")
            return False


# ── 简历操作分发 ──────────────────────────────────────────────────────────────

def execute_resume_action(tab, company: str = "", jd: str = "", target: dict | None = None) -> bool:
    """主动点击工具栏「发简历」按钮并处理弹窗。
    GENERATE_TAILORED_RESUME=True 且有 JD 时，先尝试生成定制简历并走"上传→发送→删除"流程；
    生成/上传/发送任一环节失败则跳过该对话。"""
    if GENERATE_TAILORED_RESUME and jd:
        try:
            from chat.resume_tailor import generate_tailored_resume
            from chat.session_processor import load_resume
            pdf_path = generate_tailored_resume(company, jd, load_resume())
            if pdf_path:
                tgt = target or {}
                # 用前缀「袁柯_」而非完整 stem 做模糊匹配：
                # 1) 与默认简历「袁柯.pdf」不会互相误匹配（"袁柯.pdf" 不含 "袁柯_"，反之亦然）
                # 2) 完整 stem 含公司名+时间戳，平台展示时可能截断/转义导致子串匹配失败，
                #    用稳定前缀更可靠地命中刚上传的定制简历项
                tailored_match = "袁柯_"
                ok = (upload_resume_attachment(tab, pdf_path, tgt)
                      and click_resume_btn(tab, resume_name_match=tailored_match)
                      and (random_delay(2.0, 3.0) or True)  # 等发送弹窗关闭
                      and delete_resume_attachment(tab, tailored_match, tgt))
                if ok:
                    return True
                log.warning("  [定制简历] 上传/发送/清理流程失败，跳过该对话")
                return False
        except Exception as e:
            log.error(f"  [定制简历] 流程异常: {e}，跳过该对话")
            return False
    return click_resume_btn(tab)

import time
import random
import json
import threading
import requests
from shared.logger import log

# 会话卡片选择器（chat 模块两侧共用：handler.py 轮询遍历列表 / session_actions.py 回退后重新定位卡片）
SESSION_LI = ".user-list-content > ul:nth-child(2) > li"


def evaluate(tab, js: str):
    try:
        ret = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=15)
        return ret.get("result", {}).get("value")
    except Exception as e:
        log.error(f"  [JS错误] {e}")
        return None


def cdp_click(tab, x: float, y: float):
    common = dict(x=x, y=y, button="left", clickCount=1, modifiers=0)
    tab.call_method("Input.dispatchMouseEvent", type="mousePressed", **common)
    tab.call_method("Input.dispatchMouseEvent", type="mouseReleased", **common)


def random_delay(lo: float = 1.0, hi: float = 3.0):
    time.sleep(random.uniform(lo, hi))


def scroll_into_view_and_click(tab, locate_js: str, delay: tuple[float, float] | None = (1.5, 2.5)) -> bool:
    """定位元素 → scrollIntoView 滚入视口中央 → 取重排后的最新坐标 → cdp_click。

    locate_js: 一段返回目标元素（或 null/undefined）的 JS 语句序列（不含外层 IIFE 包裹），
    例如 `"return document.querySelectorAll('li')[2];"`。
    防视口外点击失效的通用模式（与 CLAUDE.md「左侧会话卡片点击」一节一致）：
    先 scrollIntoView 让目标进入视口，等待重排完成后再用 getBoundingClientRect
    取此刻坐标点击，避免目标在视口外时坐标失效。
    """
    js = f"""
    (function() {{
        const el = (function() {{ {locate_js} }})();
        if (!el) return null;
        el.scrollIntoView({{block: 'center', behavior: 'instant'}});
        const r = el.getBoundingClientRect();
        return JSON.stringify({{x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)}});
    }})()
    """
    val = evaluate(tab, js)
    if not val or val == "null":
        return False
    try:
        pos = json.loads(val)
    except Exception:
        return False
    cdp_click(tab, pos["x"], pos["y"])
    if delay:
        random_delay(*delay)
    return True


def cdp_wheel(tab, x: float, y: float, delta_y: int):
    """发送 mouseWheel 事件。delta_y > 0 向下，< 0 向上。"""
    tab.call_method(
        "Input.dispatchMouseEvent",
        type="mouseWheel",
        x=x, y=y,
        deltaX=0, deltaY=delta_y,
        modifiers=0,
    )


def silence_pychrome_recv_loop_noise():
    """
    过滤 pychrome._recv_loop 的已知后台线程噪音：关闭标签页时 websocket-client
    在连接关闭时返回空字符串而非抛出 WebSocketException，pychrome 未捕获该情况，
    导致 json.loads('') 抛出 JSONDecodeError 冲出 _recv_loop 线程（不影响主流程）。
    只吞掉这一种特定异常，其他后台线程异常仍交给默认钩子打印。
    """
    default_hook = threading.excepthook

    def _hook(args):
        tb = args.exc_traceback
        while tb is not None:
            if tb.tb_frame.f_code.co_name == "_recv_loop":
                if args.exc_type is json.JSONDecodeError:
                    return
                break
            tb = tb.tb_next
        default_hook(args)

    threading.excepthook = _hook


def is_browser_alive(cdp_url: str) -> bool:
    """检测 CDP 端口是否可达（浏览器是否还在运行）。"""
    try:
        return requests.get(f"{cdp_url}/json", timeout=3).status_code == 200
    except Exception:
        return False


def small_human_scroll(tab, lo: int = 80, hi: int = 280):
    """点击前模拟人类浏览时的小幅随机滚动，随机方向。"""
    direction = random.choice([1, -1])
    delta     = random.randint(lo, hi)
    log.info(f"  [抖动] {'↓' if direction > 0 else '↑'} {delta}px")
    tab.call_method(
        "Input.dispatchMouseEvent",
        type="mouseWheel",
        x=random.randint(600, 900),
        y=random.randint(300, 500),
        deltaX=0,
        deltaY=direction * delta,
        modifiers=0,
    )
    time.sleep(random.uniform(0.2, 0.6))


# ── 消息读取 ──────────────────────────────────────────────────────────────────

_JS_READ_MESSAGES = """
(function() {
    // 只有这些文字的 .card-btn 才需要用户操作
    const INTERACTIVE = new Set(['同意','拒绝','委婉拒绝','感兴趣']);

    const items = Array.from(document.querySelectorAll('.message-item'));
    return JSON.stringify(items.map(msg => {
        const cls      = (msg.className || '').toString();
        const isSelf   = cls.includes('item-myself');
        const hasCardWrap  = !!msg.querySelector('.message-card-wrap');
        const hasArticles  = !!msg.querySelector('.articles-center');
        // item-system: 系统通知；hasArticles: PK竞争等系统生成卡片（class 仍是 item-friend）
        const isSystem = cls.includes('item-system') || hasArticles;

        // 只收集需要用户操作的按钮（同意/拒绝/兴趣）
        const cardBtns = Array.from(msg.querySelectorAll('.card-btn'))
            .filter(b => INTERACTIVE.has(b.innerText.trim()))
            .map(b => {
                const r = b.getBoundingClientRect();
                return {
                    text    : b.innerText.trim(),
                    disabled: b.classList.contains('disabled'),
                    x       : Math.round(r.left + r.width/2),
                    y       : Math.round(r.top  + r.height/2),
                    visible : r.width > 0 && r.height > 0,
                };
            });

        // isCard: 有 .message-card-wrap（含交互卡或联系人卡）或系统 PK 卡
        const isCard            = hasCardWrap || hasArticles;
        const isInteractiveCard = hasCardWrap && cardBtns.length > 0;

        // 文字提取：boss 普通文字在 .text p，系统提示在 .hyper-link，我方在 .text p
        const textEl  = msg.querySelector('.text p') || msg.querySelector('.hyper-link');
        const text    = textEl
            ? (textEl.innerText || '').trim().slice(0, 600)
            : (msg.querySelector('.message-content')
               ? (msg.querySelector('.message-content').innerText || '').trim().slice(0, 600)
               : '');

        const timeEl    = msg.querySelector('.time');
        const statusEl  = msg.querySelector('.message-status');
        const cardTitle = msg.querySelector('.message-card-top-title');

        return {
            mid             : msg.getAttribute('data-mid') || '',
            isSelf,
            isSystem,
            isCard,
            isInteractiveCard,
            cardTitle       : cardTitle ? cardTitle.innerText.trim() : '',
            cardBtns,
            text,
            time            : timeEl   ? timeEl.innerText.trim()   : '',
            status          : statusEl ? statusEl.innerText.trim() : '',
        };
    }));
})()
"""


def read_messages(tab) -> list[dict]:
    val = evaluate(tab, _JS_READ_MESSAGES)
    if val and isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            pass
    return []

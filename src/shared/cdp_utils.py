import time
import random
import json


def evaluate(tab, js: str):
    try:
        ret = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=15)
        return ret.get("result", {}).get("value")
    except Exception as e:
        print(f"  [JS错误] {e}")
        return None


def cdp_click(tab, x: float, y: float):
    common = dict(x=x, y=y, button="left", clickCount=1, modifiers=0)
    tab.call_method("Input.dispatchMouseEvent", type="mousePressed", **common)
    tab.call_method("Input.dispatchMouseEvent", type="mouseReleased", **common)


def random_delay(lo: float = 1.0, hi: float = 3.0):
    time.sleep(random.uniform(lo, hi))


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

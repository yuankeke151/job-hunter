import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DISCLAIMER, REPLY_ENABLED, SEND_ENABLED
from shared.cdp_utils import evaluate, cdp_click, random_delay
from shared.logger import log

# ── 常量 ──────────────────────────────────────────────────────────────────────

INPUT_SEL = "div.chat-input[contenteditable='true']"
SEND_SEL  = "button.btn-send"

# ── CDP 操作 ──────────────────────────────────────────────────────────────────

def clear_and_type(tab, text: str) -> bool:
    """清空输入框并输入文字，返回发送按钮是否可用。"""
    js_type = f"""
    (function() {{
        const el = document.querySelector({json.dumps(INPUT_SEL)});
        if (!el) return false;
        el.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('insertText', false, {json.dumps(text)});
        return true;
    }})()
    """
    ok = evaluate(tab, js_type)
    if not ok:
        return False
    time.sleep(0.5)
    enabled = evaluate(tab, f"""
        (function() {{
            const btn = document.querySelector({json.dumps(SEND_SEL)});
            return btn ? !btn.classList.contains('disabled') : false;
        }})()
    """)
    return bool(enabled)


def click_send(tab):
    js = f"""
    (function() {{
        const btn = document.querySelector({json.dumps(SEND_SEL)});
        if (!btn || btn.classList.contains('disabled')) return null;
        const r = btn.getBoundingClientRect();
        return JSON.stringify({{ x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2) }});
    }})()
    """
    val = evaluate(tab, js)
    if val and isinstance(val, str) and val != "null":
        try:
            pos = json.loads(val)
            cdp_click(tab, pos["x"], pos["y"])
            return True
        except Exception:
            pass
    log.warning("  [发送] 按钮不可用，取消发送")
    return False


# ── 输入框打入 + 日志 ─────────────────────────────────────────────────────────

def _log_box(title, text):
    bar = "─" * max(0, 54 - len(title))
    log.info(f"  ┌─ {title} {bar}")
    for line in text.split("\n"):
        log.info(f"  │  {line}")
    log.info("  └" + "─" * 57)


def type_and_log(tab, text, shot_suffix):
    if not REPLY_ENABLED:
        _log_box("跳过发送（REPLY_ENABLED=False）", text + DISCLAIMER)
        return
    full_text = text + DISCLAIMER
    clear_and_type(tab, full_text)
    _log_box("打入输入框内容", full_text)
    random_delay(1.5, 2.5)
    if not SEND_ENABLED:
        log.info("  → SEND_ENABLED=False，消息已打入输入框但不点击发送")
        return
    sent = click_send(tab)
    if sent:
        log.info("  → 消息已发送")
        random_delay(1.0, 1.5)
    else:
        log.info("  → 发送按钮不可用，消息留在输入框")

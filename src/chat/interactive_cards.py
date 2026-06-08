import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.cdp_utils import evaluate, cdp_click, random_delay
from shared.logger import log

# ── 交互卡片批量处理 ──────────────────────────────────────────────────────────

# 通过 span.dialog-icon / span.concat-icon 的次级 class 识别卡片类型
_JS_FIND_AGREE_CARDS = """
(function() {
    const results = [];
    document.querySelectorAll('.message-item').forEach(msg => {
        const wrap = msg.querySelector('.message-card-wrap');
        if (!wrap) return;

        let cardType = '';
        const dialogIconEl = wrap.querySelector('span.dialog-icon');
        if (dialogIconEl) {
            const parts = (dialogIconEl.className || '').toString().trim().split(/\\s+/);
            cardType = parts.find(c => c !== 'dialog-icon') || '';
        } else {
            const concatIconEl = wrap.querySelector('span.concat-icon');
            if (concatIconEl) {
                const parts = (concatIconEl.className || '').toString().trim().split(/\\s+/);
                const sub = parts.find(c => c !== 'concat-icon') || '';
                cardType = sub ? 'shared_' + sub : 'shared';
            }
        }

        // 找可用（未 disabled）的「同意」按钮，每张卡片最多取一个
        for (const btn of wrap.querySelectorAll('.card-btn')) {
            if (btn.innerText.trim() !== '同意') continue;
            if (btn.classList.contains('disabled')) continue;
            const r = btn.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            results.push({
                cardType,
                x: Math.round(r.left + r.width  / 2),
                y: Math.round(r.top  + r.height / 2),
            });
            break;
        }
    });
    return JSON.stringify(results);
})()
"""

_CARD_TYPE_LABEL = {
    "resume": "简历请求",
    "weixin": "微信交换",
    "note"  : "沟通意向",
}


def _read_agree_cards(tab) -> list[dict]:
    """读取当前页面所有可点击「同意」按钮的卡片（含 cardType 和坐标）。"""
    val = evaluate(tab, _JS_FIND_AGREE_CARDS)
    if val and isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            pass
    return []


def handle_interactive_cards(tab) -> bool:
    """
    处理当前聊天中所有可点击「同意」的非简历交互卡片（微信交换、沟通意向等）。

    流程：
      - 直接循环：每轮重新读取可点击同意卡片，按顺序找第一张非简历卡
          找到 → 点击「同意」，等待 1.5-2.5s（系统可能自动插入消息，坐标会变化），继续下一轮
          找不到（全是简历卡或无卡片）→ 结束循环
      简历请求卡始终跳过，不在此处处理。

    返回值固定为 False。
    """
    while True:
        cards  = _read_agree_cards(tab)
        target = next((c for c in cards if c["cardType"] != "resume"), None)
        if target is None:
            break
        label = _CARD_TYPE_LABEL.get(target["cardType"], f"未知({target['cardType']})")
        log.info(f"  [卡片交互] 点击「{label}」  center=({target['x']},{target['y']})")
        cdp_click(tab, target["x"], target["y"])
        random_delay(1.5, 2.5)

    return False

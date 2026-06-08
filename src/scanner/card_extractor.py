"""
card_extractor.py — 卡片列表提取与无限滚动翻页
"""
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner.page_js import JS_EXTRACT_CARDS, JS_SCROLL_BOTTOM
from shared.cdp_utils import evaluate, cdp_wheel

SCROLL_DELTA = 2000   # 每次滚动像素
SCROLL_WAIT  = 2.5    # 滚动后等待加载秒数


def extract_cards(tab):
    """提取当前页面所有卡片，返回 list[dict]（提取失败返回 []）。"""
    raw = evaluate(tab, JS_EXTRACT_CARDS)
    return json.loads(raw) if raw else []


def cdp_click_scroll(tab, x: float, y: float):
    """发送 mouseWheel 事件触发无限滚动翻页。"""
    cdp_wheel(tab, x, y, SCROLL_DELTA)


def scroll_for_more(tab) -> int:
    """向下滚动一次并等待加载，返回滚动后的卡片总数。"""
    cdp_click_scroll(tab, 760, 400)
    evaluate(tab, JS_SCROLL_BOTTOM)
    time.sleep(SCROLL_WAIT)
    return len(extract_cards(tab))

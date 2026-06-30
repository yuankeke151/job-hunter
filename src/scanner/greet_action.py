"""
greet_action.py — 「立即沟通」操作：点击按钮 → 检测弹窗/跳转/无响应 → 返回 greet_status
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner.page_js import (
    JS_CHAT_BTN_RECT, JS_STAY_BTN_RECT, JS_EXPECT_TAB_RECT,
)
from shared.cdp_utils import evaluate, cdp_click, random_delay
from shared.logger import log


def try_greet(tab) -> int:
    """
    点击「立即沟通」按钮并处理后续场景，返回 greet_status：
        0 = 未打招呼（按钮未找到/不可见/点击无响应）
        1 = 本次打招呼（首次沟通，弹窗「留在此页」）
        2 = 他端已打招呼（直接跳转，已自动导航回列表并恢复求职期望 tab）
    """
    log.info("      [沟通] 尝试点击「立即沟通」...")
    btn_raw = evaluate(tab, JS_CHAT_BTN_RECT)
    if not btn_raw:
        log.warning("      [沟通] 未找到「立即沟通」按钮，跳过")
        return 0
    btn = json.loads(btn_raw)
    if not btn.get("visible"):
        log.warning("      [沟通] 按钮不可见，跳过")
        return 0
    url_before = evaluate(tab, "window.location.href") or ""
    cdp_click(tab, btn["x"], btn["y"])
    random_delay(1.0, 1.5)

    url_after = evaluate(tab, "window.location.href") or ""
    stay_raw  = evaluate(tab, JS_STAY_BTN_RECT)

    if stay_raw:
        stay = json.loads(stay_raw)
        cdp_click(tab, stay["x"], stay["y"])
        log.info("      [弹窗] 已点击「留在此页」")
        random_delay(0.5, 1.0)
        return 1

    if url_after != url_before:
        log.info("      [跳转] 检测到页面跳转 → 他端已打过招呼")
        log.info("      [返回] 导航回岗位列表...")
        tab.call_method("Page.navigate", url=url_before, timeout=15)
        random_delay(2.5, 3.5)

        # 页面回到推荐 tab，需点击求职期望 tab 刷新列表
        tab_raw = evaluate(tab, JS_EXPECT_TAB_RECT)
        if tab_raw:
            t = json.loads(tab_raw)
            cdp_click(tab, t["x"], t["y"])
            log.info("      [返回] 已点击「数据分析师」tab，等待列表刷新...")
            random_delay(2.0, 2.5)
        else:
            log.warning("      [返回] 未找到求职期望 tab，列表可能停在推荐页")

        log.info("      [返回] 已回到岗位列表")
        return 2

    log.warning("      [沟通] 点击后无响应，跳过")
    return 0

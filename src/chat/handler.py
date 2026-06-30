"""
chat_handler.py — BOSS直聘 IM 聊天自动化处理

运行策略：
  1. DIRECT_MODE=True → 直接处理当前右侧可见会话一次后退出（测试用）
  2. DIRECT_MODE=False（默认）→ 轮询左侧会话列表，处理完一轮后退出：
     get_all_sessions → 过滤已处理(processed_eids) → for 循环按 DOM 顺序依次处理
     每次处理通过 encryptJobId 定位会话实际位置（列表可能已重排）
     处理数达到 POLL_LIMIT 或列表耗尽 → 退出程序

已验证的选择器（debug_chat2.py）：
  会话卡片  : .user-list-content > ul:nth-child(2) > li
  姓名      : .name-text
  公司      : .name-box > span:nth-child(2)
  职位身份  : .name-box > span:last-child
  时间      : .time
  消息预览  : .last-msg-text
  未读角标  : .notice-badge
  聊天容器  : .chat-content
  消息条目  : .message-item
  同意/拒绝 : span.card-btn
  输入框    : div.chat-input[contenteditable='true']
  发送按钮  : button.btn-send
"""
import sys
import time
import json

import requests
import pychrome
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_CHAT_URL, POLL_LIMIT, DIRECT_MODE
from shared.cdp_utils import (random_delay, evaluate,
                              small_human_scroll, is_browser_alive,
                              silence_pychrome_recv_loop_noise,
                              scroll_into_view_and_click, SESSION_LI)
from shared.database import init_chat_db
from shared.logger import log
from chat.session_processor import process_session

# ── 常量 ──────────────────────────────────────────────────────────────────────
CDP_URL    = CDP_CHAT_URL   # 与 scanner 共用同一 Chrome（start_chrome.bat 启动，port 9222）

_JS_GET_SESSIONS = f"""
(function() {{
    const lis = Array.from(document.querySelectorAll({json.dumps(SESSION_LI)}));
    return JSON.stringify(lis.map((li, idx) => {{
        const q    = s => {{ const e = li.querySelector(s); return e ? (e.innerText||'').trim() : ''; }};
        const spans = Array.from(li.querySelectorAll('.name-box > span'));
        const r    = li.getBoundingClientRect();
        const src  = (li.__vue__ && li.__vue__.$props && li.__vue__.$props.source) || {{}};
        return {{
            idx,
            name         : q('.name-text'),
            company      : spans.length > 1 ? (spans[1].innerText||'').trim() : '',
            title        : spans.length > 2 ? (spans[spans.length-1].innerText||'').trim() : '',
            time         : q('.time'),
            preview      : q('.last-msg-text'),
            unread       : q('.notice-badge'),
            center       : {{ x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2) }},
            visible      : r.top >= 0 && r.top < window.innerHeight,
            encryptJobId : src.encryptJobId || '',
        }};
    }}));
}})()
"""


def get_all_sessions(tab) -> list[dict]:
    val = evaluate(tab, _JS_GET_SESSIONS)
    if val and isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            pass
    return []



def connect_tab() -> tuple:
    """连接 CDP，返回 (browser, tab)。"""
    try:
        tabs_info = requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        log.error(f"[失败] 无法连接 {CDP_URL}: {e}"); sys.exit(1)

    pages   = [t for t in tabs_info if t.get("type") == "page"]
    im_info = next(
        (t for t in pages if "/web/geek/chat" in t.get("url", "")), None
    ) or next(
        (t for t in pages if "zhipin.com"     in t.get("url", "")), None
    )
    if not im_info:
        log.error("[失败] 未找到 BOSS直聘 IM 标签页"); sys.exit(1)

    log.info(f"[标签页] {im_info.get('title','')[:50]}")
    log.info(f"[URL]    {im_info.get('url','')}")

    browser = pychrome.Browser(url=CDP_URL)
    tab     = next((t for t in browser.list_tab() if t.id == im_info["id"]), None)
    if not tab:
        log.error("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    log.info("[CDP] 连接成功")
    return browser, tab


def main():
    silence_pychrome_recv_loop_noise()

    log.info("=" * 60)
    log.info("  handler.py — BOSS直聘 IM 自动化处理")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    init_chat_db()

    _, tab = connect_tab()

    try:
        if DIRECT_MODE:
            # 测试模式：不点击左侧，直接处理当前右侧可见会话，处理后退出
            log.info("[测试] DIRECT_MODE=True，处理当前右侧会话后退出")
            process_session(tab, session_info=None)
            log.info("[退出] 处理完成，程序结束")
            sys.exit(0)

        sessions = get_all_sessions(tab)
        if not sessions:
            log.info("[轮询] 未找到任何会话，退出")
            sys.exit(0)

        log.info(f"[轮询] 共 {len(sessions)} 个可见会话")

        processed_eids = set()
        processed = 0
        _JS_CUR_ID = ("(window.chat&&window.chat.communicating"
                      "&&window.chat.communicating.encryptJobId)||''")

        while processed < POLL_LIMIT:
            sessions = get_all_sessions(tab)
            candidates = [s for s in sessions
                          if s["encryptJobId"] not in processed_eids]

            if candidates:
                for s in candidates:
                    if processed >= POLL_LIMIT:
                        break
                    processed_eids.add(s["encryptJobId"])
                    log.info(f"[轮询] ({processed+1}/{POLL_LIMIT}) "
                             f"{s['name']}  {s['company']}  "
                             f"未读={s.get('unread') or '0'}  time={s.get('time','')!r}")
                    try:
                        small_human_scroll(tab, lo=100, hi=350)
                        target_id = s.get("encryptJobId", "")
                        if not target_id:
                            log.error(f"  [致命] 会话 {s.get('name','')} 无 encryptJobId，"
                                      f"页面结构异常，程序退出")
                            sys.exit(1)
                        locate_js = (
                            f"return Array.from(document.querySelectorAll({json.dumps(SESSION_LI)}))"
                            f".find(li => li.__vue__ && li.__vue__.$props"
                            f" && li.__vue__.$props.source"
                            f" && li.__vue__.$props.source.encryptJobId === {json.dumps(target_id)});"
                        )
                        if not scroll_into_view_and_click(tab, locate_js, delay=None):
                            log.warning(f"  [跳过] 未找到 encryptJobId={target_id[:20]} 的卡片"
                                        f"（列表可能已重排），跳过本会话")
                            continue
                        for _ in range(16):
                            cur_id = evaluate(tab, _JS_CUR_ID)
                            if cur_id == target_id:
                                break
                            time.sleep(0.5)
                        else:
                            log.warning(f"  [跳过] 右侧未切换到目标会话 {target_id[:20]}，"
                                        f"当前={str(evaluate(tab, _JS_CUR_ID))[:20]}")
                            continue
                        random_delay(0.3, 0.5)
                        process_session(tab, session_info=s)
                        processed += 1
                        random_delay(2.0, 3.0)
                    except Exception as e:
                        if not is_browser_alive(CDP_URL):
                            log.error(f"[退出] 浏览器已关闭（会话处理中断）: {e}")
                            sys.exit(0)
                        log.error(f"  [错误] 会话 {s['name']} 处理异常，跳过: {e}")
                continue

            log.info(f"[轮询] 无更多未处理会话，本轮结束（已处理 {processed} 个）")
            break

        log.info(f"[退出] 处理完成（共 {processed} 个会话），程序结束")

    finally:
        try: tab.stop()
        except Exception: pass


if __name__ == "__main__":
    main()

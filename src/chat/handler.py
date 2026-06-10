"""
chat_handler.py — BOSS直聘 IM 聊天自动化处理

流程：
  连接 CDP → 处理当前可见会话 → 遍历左侧有未读角标的会话：
    读取聊天信息 → 点击同意（简历/交换请求）→ AI 回复（人工确认）→ 写库

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
from config import CDP_CHAT_URL, POLL_LIMIT, CONTINUOUS_POLL
from shared.cdp_utils import (cdp_click, random_delay, evaluate, small_human_scroll,
                              is_browser_alive, silence_pychrome_recv_loop_noise,
                              scroll_into_view_and_click, SESSION_LI)
from shared.database import init_chat_db
from shared.logger import log
from chat.session_processor import process_session

# ── 常量 ──────────────────────────────────────────────────────────────────────
CDP_URL    = CDP_CHAT_URL   # port 9223，由 start_chrome_chat.bat 启动

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
    log.info("  chat_handler.py — BOSS直聘 IM 自动化处理")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    init_chat_db()

    _, tab = connect_tab()
    try:
        round_num = 0
        while True:
            if not is_browser_alive(CDP_URL):
                log.info("[退出] 检测到浏览器已关闭，程序退出")
                break

            round_num += 1
            log.info(f"{'='*60}")
            log.info(f"  [第 {round_num} 轮] {datetime.now().strftime('%H:%M:%S')}")
            log.info(f"{'='*60}")

            try:
                sessions = get_all_sessions(tab)
            except Exception as e:
                log.error(f"[退出] 获取会话列表失败（浏览器可能已关闭）: {e}")
                break

            if not sessions:
                log.info("[轮询] 未找到任何会话，等待 10s 后重试...")
                time.sleep(10)
                continue

            if not CONTINUOUS_POLL:
                # 单次模式：不点击左侧，直接处理当前右侧可见会话，处理后退出
                log.info("[轮询] 单次模式（CONTINUOUS_POLL=False），处理当前右侧会话后退出")
                try:
                    process_session(tab, session_info=None)
                except Exception as e:
                    if not is_browser_alive(CDP_URL):
                        log.error(f"[退出] 浏览器已关闭: {e}")
                    else:
                        log.error(f"  [错误] 会话处理异常: {e}")
                break

            log.info(f"[轮询] 共 {len(sessions)} 个会话，本轮目标处理前 {POLL_LIMIT} 个")

            processed_states = {}  # encryptJobId -> 上次处理时的 unread 数
            processed = 0          # 本轮实际处理数
            _JS_CUR_ID = ("(window.chat&&window.chat.communicating"
                          "&&window.chat.communicating.encryptJobId)||''")

            while processed < POLL_LIMIT:
                sessions = get_all_sessions(tab)

                def _needs(s):
                    eid = s["encryptJobId"]
                    cur = int(s.get("unread") or 0)
                    if eid not in processed_states:
                        return True
                    return cur > 0 and cur > processed_states[eid]

                candidates = [s for s in sessions if _needs(s)]
                candidates.sort(key=lambda s: 0 if int(s.get("unread") or 0) > 0 else 1)

                if not candidates:
                    log.info(f"[轮询] 无需处理的会话，本轮结束（已处理 {processed} 个）")
                    break

                s = candidates[0]
                processed_states[s["encryptJobId"]] = int(s.get("unread") or 0)
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
                        log.warning(f"  [警告] 右侧未切换到目标会话 {target_id[:20]}，"
                                    f"当前={str(evaluate(tab, _JS_CUR_ID))[:20]}")
                    random_delay(0.3, 0.5)
                    process_session(tab, session_info=s)
                    processed += 1
                    random_delay(2.0, 3.0)
                except Exception as e:
                    if not is_browser_alive(CDP_URL):
                        log.error(f"[退出] 浏览器已关闭（会话处理中断）: {e}")
                        sys.exit(0)
                    log.error(f"  [错误] 会话 {s['name']} 处理异常，跳过: {e}")

            log.info(f"[轮询] 已处理 {processed} 个会话，从头开始下一轮")
            random_delay(3.0, 5.0)

    finally:
        try: tab.stop()
        except Exception: pass


if __name__ == "__main__":
    main()

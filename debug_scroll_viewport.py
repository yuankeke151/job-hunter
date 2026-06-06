"""
debug_scroll_viewport.py — 测试 chat 页面三处滚轮及视口检测

测试项：
  1. 左侧会话列表：枚举卡片视口状态，滑轮上下滚动，scrollIntoView 定位
  2. 右侧聊天记录：检测滚轮是否存在（内容超长才出现），向上滚到顶
  3. 输入框：检测多行时是否出现内部滚轮

运行前提：
  - start_chrome_chat.bat 已启动（port 9223）
  - 已手动导航到 /web/geek/chat 并打开一个会话

用法：
  python debug_scroll_viewport.py
"""
import sys
import time
import json
import random
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from config import CDP_CHAT_URL

CDP_URL    = CDP_CHAT_URL
SEL_LIST   = ".user-list-content > ul:nth-child(2) > li"
SEL_CHAT   = ".chat-content"
SEL_INPUT  = "div.chat-input[contenteditable='true']"


# ── CDP 基础工具 ──────────────────────────────────────────────────────────────

def evaluate(tab, js: str):
    ret = tab.call_method("Runtime.evaluate", expression=js,
                          returnByValue=True, timeout=10)
    return ret.get("result", {}).get("value")


def wheel(tab, x: float, y: float, delta_y: int):
    """向指定坐标发送滚轮事件。delta_y > 0 向下，< 0 向上。"""
    tab.call_method(
        "Input.dispatchMouseEvent",
        type="mouseWheel",
        x=x, y=y,
        deltaX=0, deltaY=delta_y,
        modifiers=0,
    )


def sep(title=""):
    line = "─" * 60
    print(f"\n{line}")
    if title:
        print(f"  {title}")
        print(line)


# ── 测试 1：左侧会话列表视口状态 ──────────────────────────────────────────────

def test_left_list_viewport(tab):
    sep("TEST 1 — 左侧会话列表视口状态")

    js = f"""
    (function() {{
        const wh = window.innerHeight;
        const lis = Array.from(document.querySelectorAll({json.dumps(SEL_LIST)}));
        return JSON.stringify(lis.map((li, i) => {{
            const r = li.getBoundingClientRect();
            const nameEl = li.querySelector('.name-text');
            return {{
                idx     : i,
                name    : nameEl ? nameEl.innerText.trim() : '(无)',
                top     : Math.round(r.top),
                bottom  : Math.round(r.bottom),
                inView  : r.top >= 0 && r.bottom <= wh,
                partial : (r.top < wh && r.bottom > 0) && !(r.top >= 0 && r.bottom <= wh),
                offView : r.bottom <= 0 || r.top >= wh,
            }};
        }}));
    }})()
    """
    raw = evaluate(tab, js)
    if not raw:
        print("  [错误] 未获取到会话卡片列表")
        return

    cards = json.loads(raw)
    print(f"  共 {len(cards)} 张会话卡片，窗口高度: {evaluate(tab, 'window.innerHeight')}px\n")
    print(f"  {'idx':>3}  {'状态':<8}  {'top':>6}  {'bottom':>6}  {'name'}")
    print(f"  {'─'*3}  {'─'*8}  {'─'*6}  {'─'*6}  {'─'*20}")
    for c in cards:
        if c['inView']:
            status = "OK 视口内"
        elif c['partial']:
            status = "~~ 部分可"
        else:
            status = "-- 视口外"
        print(f"  {c['idx']:>3}  {status:<8}  {c['top']:>6}  {c['bottom']:>6}  {c['name']}")

    off = [c for c in cards if c['offView']]
    print(f"\n  视口外卡片数: {len(off)}")
    return cards


# ── 测试 2：左侧列表滚轮 ──────────────────────────────────────────────────────

def test_left_scroll_wheel(tab):
    sep("TEST 2 — 左侧列表滚轮（向下 → 向上 → 恢复）")

    js_list_center = f"""
    (function() {{
        const el = document.querySelector('.user-list-content');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return JSON.stringify({{ x: Math.round(r.left + r.width/2),
                                 y: Math.round(r.top  + r.height/2) }});
    }})()
    """
    raw = evaluate(tab, js_list_center)
    if not raw:
        print("  [错误] 未找到 .user-list-content")
        return
    pos = json.loads(raw)
    cx, cy = pos["x"], pos["y"]
    print(f"  列表中心坐标: ({cx}, {cy})")

    for label, delta in [("向下 400px", 400), ("向下 400px", 400),
                         ("向上 300px", -300), ("向上 500px", -500)]:
        print(f"  滚轮 {label} ...")
        wheel(tab, cx, cy, delta)
        time.sleep(0.8)

    print("  OK 左侧列表滚轮测试完毕")


# ── 测试 3：左侧卡片 scrollIntoView ──────────────────────────────────────────

def test_left_scroll_into_view(tab, cards):
    sep("TEST 3 — scrollIntoView 定位视口外卡片")

    off = [c for c in (cards or []) if c['offView']]
    if not off:
        print("  所有卡片均在视口内，跳过此测试")
        return

    target = off[-1]   # 取最后一张（通常在最下方）
    print(f"  目标卡片: idx={target['idx']}  name={target['name']!r}")
    print(f"  原始位置: top={target['top']}  bottom={target['bottom']}")

    js = f"""
    (function() {{
        const lis = Array.from(document.querySelectorAll({json.dumps(SEL_LIST)}));
        const el  = lis[{target['idx']}];
        if (!el) return null;
        el.scrollIntoView({{ block: 'center', behavior: 'instant' }});
        const r = el.getBoundingClientRect();
        return JSON.stringify({{ top: Math.round(r.top), bottom: Math.round(r.bottom),
                                 inView: r.top >= 0 && r.bottom <= window.innerHeight }});
    }})()
    """
    time.sleep(0.3)
    raw = evaluate(tab, js)
    if not raw:
        print("  [错误] scrollIntoView 执行失败")
        return
    after = json.loads(raw)
    print(f"  scrollIntoView 后: top={after['top']}  bottom={after['bottom']}  "
          f"inView={after['inView']}")
    print(f"  scrollIntoView: {'OK 成功' if after['inView'] else 'FAIL 失败'}")


# ── 测试 4：右侧聊天记录滚轮 ─────────────────────────────────────────────────

def test_right_chat_scroll(tab):
    sep("TEST 4 — 右侧聊天记录滚轮")

    js = f"""
    (function() {{
        const el = document.querySelector({json.dumps(SEL_CHAT)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const overflows = el.scrollHeight > el.clientHeight;
        return JSON.stringify({{
            x         : Math.round(r.left + r.width/2),
            y         : Math.round(r.top  + r.height/2),
            scrollH   : el.scrollHeight,
            clientH   : el.clientHeight,
            overflows : overflows,
            atBottom  : el.scrollTop + el.clientHeight >= el.scrollHeight - 5,
        }});
    }})()
    """
    raw = evaluate(tab, js)
    if not raw:
        print("  [错误] 未找到 .chat-content（可能未打开会话）")
        return

    info = json.loads(raw)
    print(f"  chat-content scrollHeight={info['scrollH']}  clientHeight={info['clientH']}")
    print(f"  可滚动: {'是' if info['overflows'] else '否（内容未超出）'}")
    print(f"  当前位置: {'底部' if info['atBottom'] else '非底部'}")

    if info['overflows']:
        cx, cy = info["x"], info["y"]
        print(f"  滚轮中心坐标: ({cx}, {cy})")
        print("  向上滚动（查看历史消息）...")
        wheel(tab, cx, cy, -800)
        time.sleep(0.8)
        print("  向下滚动（回到底部）...")
        wheel(tab, cx, cy, 800)
        time.sleep(0.5)
        print("  OK 右侧聊天记录滚轮测试完毕")
    else:
        print("  内容未超出视口，右侧滚轮不存在（正常）")


# ── 测试 5：输入框滚轮 ───────────────────────────────────────────────────────

def test_input_scroll(tab):
    sep("TEST 5 — 输入框滚轮（多行时才出现）")

    js = f"""
    (function() {{
        const el = document.querySelector({json.dumps(SEL_INPUT)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return JSON.stringify({{
            x        : Math.round(r.left + r.width/2),
            y        : Math.round(r.top  + r.height/2),
            scrollH  : el.scrollHeight,
            clientH  : el.clientHeight,
            overflows: el.scrollHeight > el.clientHeight + 2,
            text     : (el.innerText || '').slice(0, 40),
        }});
    }})()
    """
    raw = evaluate(tab, js)
    if not raw:
        print("  [错误] 未找到输入框")
        return

    info = json.loads(raw)
    print(f"  输入框内容预览: {info['text']!r}")
    print(f"  scrollHeight={info['scrollH']}  clientHeight={info['clientH']}")
    print(f"  内部滚轮: {'存在（内容超出）' if info['overflows'] else '不存在（内容未超出）'}")

    if info['overflows']:
        cx, cy = info["x"], info["y"]
        print(f"  滚轮坐标: ({cx}, {cy})")
        wheel(tab, cx, cy, -200)
        time.sleep(0.4)
        wheel(tab, cx, cy, 200)
        print("  OK 输入框滚轮测试完毕")


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  debug_scroll_viewport.py")
    print("=" * 60)

    try:
        tabs_info = requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        print(f"[失败] 无法连接 {CDP_URL}: {e}"); sys.exit(1)

    im_info = next(
        (t for t in tabs_info if "/web/geek/chat" in t.get("url", "")
         and t.get("type") == "page"), None
    )
    if not im_info:
        print("[失败] 未找到 /web/geek/chat 标签页"); sys.exit(1)

    print(f"[标签页] {im_info.get('title','')[:50]}")

    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == im_info["id"]), None)
    if not tab:
        print("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    print("[CDP] 连接成功\n")

    try:
        cards = test_left_list_viewport(tab)
        time.sleep(0.5)
        test_left_scroll_wheel(tab)
        time.sleep(0.5)
        test_left_scroll_into_view(tab, cards)
        time.sleep(0.5)
        test_right_chat_scroll(tab)
        time.sleep(0.5)
        test_input_scroll(tab)

        sep("全部测试完成")
    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()

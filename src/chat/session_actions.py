import sys, json, re, time, random
from pathlib import Path
from openai import OpenAI
import pychrome
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (API_KEY, API_BASE_URL, AI_MODEL, CDP_CHAT_URL,
                    DISCLAIMER, REPLY_ENABLED, SEND_ENABLED)
from shared.cdp_utils import evaluate, cdp_click, random_delay, read_messages
from shared.logger import log

# ── 常量 ──────────────────────────────────────────────────────────────────────

INPUT_SEL = "div.chat-input[contenteditable='true']"
SEND_SEL  = "button.btn-send"

_ai_client: OpenAI | None = None

# ── AI Prompt ─────────────────────────────────────────────────────────────────

_SYS_PROMPT = """\
你是专业求职助手，帮助用户与招聘HR进行自然、专业的中文沟通。

根据职位JD（含薪资范围，如有）、用户简历、完整聊天记录，完成以下任务：
0. 若提供了薪资范围，可作为评估职位匹配度和生成回复内容的参考依据
   （如薪资明显契合或聊天中提到薪资话题时自然回应，无需主动炫耀或纠结数字）
1. 评估HR倾向性分数（0-100）：
   - 0-30  ：不感兴趣/敷衍
   - 30-60 ：例行流程/一般
   - 60-80 ：较感兴趣/主动跟进
   - 80-100：非常感兴趣/积极推进
2. 若 need_self_promo=true：生成简历投递后的自我推荐，100-150字，自然专业，
   突出与岗位最相关的 2-3 个经历或技能，结尾表达期待沟通
3. 若 need_reply=true：针对HR最新消息生成回复，50-150字，语气自然

只输出合法JSON，不含任何markdown或额外文字：
{"self_promo": "...", "reply": "...", "tendency_score": 75, "reasoning": "一句话说明评分依据"}

不需要的字段填空字符串 ""。\
"""

# ── AI 工具 ───────────────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    global _ai_client
    if _ai_client is None:
        _ai_client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
    return _ai_client


def _fmt_history(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        sender = "我" if m["isSelf"] else "HR"
        if m["isCard"]:
            label = m.get("cardTitle") or m.get("text", "")[:40]
            lines.append(f"[{sender}][{m.get('time','')}][卡片] {label}")
        elif m.get("text"):
            status = f" ({m['status']})" if m.get("status") else ""
            lines.append(f"[{sender}][{m.get('time','')}]{status} {m['text']}")
    return "\n".join(lines)


def call_ai(
    boss_info: dict,
    jd: str,
    resume: str,
    messages: list[dict],
    need_reply: bool,
    need_self_promo: bool = False,
) -> dict:
    """调用 AI，返回 {self_promo, reply, tendency_score, reasoning}。"""
    salary_desc = boss_info.get("salaryDesc", "")
    salary_line = f"薪资范围：{salary_desc}\n" if salary_desc else ""
    jd_section  = f"【职位JD】\n公司：{boss_info.get('companyName','')}\n{salary_line}{jd[:2500]}\n\n"

    user_content = (
        f"need_self_promo: {'true' if need_self_promo else 'false'}\n"
        f"need_reply: {'true' if need_reply else 'false'}\n\n"
        f"{jd_section}"
        f"【我的简历】\n{resume[:2500]}\n\n"
        f"【完整聊天记录】\n{_fmt_history(messages)}"
    )
    _empty = {"self_promo": "", "reply": "", "tendency_score": 0, "reasoning": ""}
    try:
        resp = _get_client().chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": _SYS_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=800,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            log.warning("  [AI] 响应为空，跳过")
            return _empty
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        if not raw:
            log.warning("  [AI] 响应去除代码块后为空，跳过")
            return _empty
        result = json.loads(raw)
        return {
            "self_promo"     : result.get("self_promo", ""),
            "reply"          : result.get("reply", ""),
            "tendency_score" : min(max(int(result.get("tendency_score", 0)), 0), 100),
            "reasoning"      : result.get("reasoning", ""),
        }
    except json.JSONDecodeError as e:
        log.error(f"  [AI] JSON 解析失败: {e}  原始响应: {raw!r:.100}")
        return _empty
    except Exception as e:
        log.error(f"  [AI] 调用失败: {e}")
        return _empty


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


def handle_resume_dialog(tab) -> bool:
    """
    等待简历选择弹窗出现，点击「袁柯.pdf」简历项，再点击「发送」确认。

    已验证的选择器（debug_chat5.py）：
      弹窗检测 : .boss-popup__wrapper  (z=2014, 580×318)
      简历项   : span.resume-name  含「袁柯」
      选中态   : [class*="select-one"] 出现
      确认按钮 : .btn-confirm  text='发送'
    """
    log.info("  [简历弹窗] 等待弹窗出现...")

    for _ in range(10):
        time.sleep(0.5)
        js_check = """
        (function() {
            const el = document.querySelector('.boss-popup__wrapper');
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return (r.width > 0 && r.height > 0
                    && r.top < window.innerHeight && r.bottom > 0)
                   ? 'visible' : null;
        })()
        """
        if evaluate(tab, js_check) == "visible":
            log.info("  [简历弹窗] 检测到弹窗 (.boss-popup__wrapper)")
            break
    else:
        log.warning("  [简历弹窗] 等待超时，弹窗未出现")
        return False

    time.sleep(0.5)

    js_find = """
    (function() {
        for (const el of document.querySelectorAll('span.resume-name')) {
            const txt = (el.innerText || '').trim();
            if (!txt.includes('袁柯')) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            return JSON.stringify({
                x: Math.round(r.left + r.width/2),
                y: Math.round(r.top  + r.height/2),
                txt: txt.slice(0, 40),
                cls: (el.className||'').trim().slice(0, 60),
            });
        }
        const popup = document.querySelector('.boss-popup__wrapper');
        if (!popup) return null;
        let best = null, bestScore = -99;
        popup.querySelectorAll('*').forEach(el => {
            const txt = (el.innerText || '').trim();
            if (!txt.includes('袁柯')) return;
            const r = el.getBoundingClientRect();
            if (r.width < 20 || r.height < 8 || r.width > 600) return;
            const cls = (el.className||'').toString();
            const score = (cls.includes('resume') ? 10 : 0)
                        + (el.children.length === 0 ? 5 : 0)
                        - el.children.length * 2;
            if (score > bestScore) {
                bestScore = score;
                best = { x: Math.round(r.left+r.width/2),
                         y: Math.round(r.top+r.height/2),
                         txt: txt.slice(0,40),
                         cls: cls.replace(/\\s+/g,' ').trim().slice(0,60) };
            }
        });
        return best ? JSON.stringify(best) : null;
    })()
    """
    val = evaluate(tab, js_find)
    if not val or val == "null":
        log.warning("  [简历弹窗] ✗ 未找到「袁柯」简历项")
        return False

    try:
        item = json.loads(val)
        log.info(f"  [简历弹窗] ✓ 点击简历: {item['txt']!r}  "
                 f"cls={item['cls']!r}  center=({item['x']},{item['y']})")
        cdp_click(tab, item["x"], item["y"])
        time.sleep(0.8)
    except Exception as e:
        log.error(f"  [简历弹窗] 点击简历项失败: {e}")
        return False

    js_confirm = """
    (function() {
        let btn = document.querySelector('.btn-confirm');
        if (btn) {
            const r = btn.getBoundingClientRect();
            if (r.width > 0)
                return JSON.stringify({
                    x: Math.round(r.left+r.width/2),
                    y: Math.round(r.top+r.height/2),
                    txt: (btn.innerText||'').trim(),
                    cls: (btn.className||'').trim().slice(0,60),
                });
        }
        const popup = document.querySelector('.boss-popup__wrapper');
        if (!popup) return null;
        for (const b of popup.querySelectorAll('button, .btn, .btn-v2')) {
            const txt = (b.innerText||'').trim();
            const r   = b.getBoundingClientRect();
            if (txt === '发送' && r.width > 0)
                return JSON.stringify({
                    x: Math.round(r.left+r.width/2),
                    y: Math.round(r.top+r.height/2),
                    txt, cls: (b.className||'').trim().slice(0,60),
                });
        }
        return null;
    })()
    """
    time.sleep(0.3)

    val2 = evaluate(tab, js_confirm)
    if not val2 or val2 == "null":
        log.warning("  [简历弹窗] ✗ 未找到「发送」确认按钮")
        return False

    try:
        btn = json.loads(val2)
        log.info(f"  [简历弹窗] ✓ 点击确认: {btn['txt']!r}  cls={btn['cls']!r}  "
                 f"center=({btn['x']},{btn['y']})")
        cdp_click(tab, btn["x"], btn["y"])
        random_delay(1.5, 2.5)
        return True
    except Exception as e:
        log.error(f"  [简历弹窗] 点击确认失败: {e}")
        return False


def click_resume_btn(tab) -> bool:
    """直接点击工具栏「发简历」按钮并处理弹窗（无消息数量前提条件）。"""
    js_btn = """
    (function() {
        for (const el of document.querySelectorAll('.toolbar-btn-content')) {
            const txt = (el.innerText || '').trim();
            if (txt === '发简历' || txt.includes('发简历')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    return JSON.stringify({
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top  + r.height/2),
                    });
            }
        }
        return null;
    })()
    """
    val = evaluate(tab, js_btn)
    if not val or val == "null":
        log.warning("  [发简历] 未找到工具栏按钮")
        return False
    btn = json.loads(val)
    log.info(f"  [发简历] 点击按钮 center=({btn['x']},{btn['y']})")
    cdp_click(tab, btn["x"], btn["y"])
    random_delay(1.0, 1.5)
    return handle_resume_dialog(tab)


# ── 简历操作分发 ──────────────────────────────────────────────────────────────

def execute_resume_action(tab) -> bool:
    """主动点击工具栏「发简历」按钮并处理弹窗。"""
    return click_resume_btn(tab)


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


# ── 「查看职位」详情页抓取 ────────────────────────────────────────────────────

_JS_FIND_VIEW_JOB_BTN = """
(function() {
    const spans = Array.from(document.querySelectorAll('.position-content .right-content span'));
    const el = spans.find(s => (s.innerText || '').includes('查看职位'));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return JSON.stringify({
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        visible: r.width > 0 && r.height > 0,
    });
})()
"""

_JS_EXTRACT_JOB_DETAIL = """
(function() {
    const txt = (s) => { const e = document.querySelector(s); return e ? (e.innerText || '').trim() : ''; };

    const jobName = txt('.job-name') || txt('.job-banner .name h1');
    const salary  = txt('.job-banner .name .salary') || txt('.salary');
    const city    = txt('.job-banner .text-desc.text-city') || txt('.text-city');
    const jd      = txt('.job-sec-text');

    let companyName = '', recruiterName = '', recruiterTitle = '';
    const boss = document.querySelector('.job-boss-info');
    if (boss) {
        const nameEl = boss.querySelector('h2.name');
        if (nameEl) {
            for (const node of nameEl.childNodes) {
                if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
                    recruiterName = node.textContent.trim();
                    break;
                }
            }
        }
        const attrText = (boss.querySelector('.boss-info-attr')?.innerText || '').trim();
        const parts = attrText.split('\\u00b7').map(s => s.trim()).filter(Boolean);
        if (parts.length >= 2) {
            companyName    = parts[0];
            recruiterTitle = parts[1];
        } else if (parts.length === 1) {
            companyName = parts[0];
        }
    }

    return JSON.stringify({jobName, city, salary, companyName, jd, recruiterName, recruiterTitle});
})()
"""


def fetch_job_detail_via_view_job(tab) -> dict | None:
    """
    点击聊天页右侧职位面板的「查看职位」，在新打开的 job_detail 标签页中
    提取完整岗位信息（岗位名称/地点/薪资/公司名称/JD/招聘者姓名/招聘者title），
    随后关闭该标签页并切回聊天页。

    该页面薪资为明文（与扫描页 .job-salary 的 kanzhun-mix 混淆不同），无需解码。
    任何步骤失败均返回 None，并尽量保证标签页已清理、焦点已切回聊天页。
    """
    raw = evaluate(tab, _JS_FIND_VIEW_JOB_BTN)
    if not raw or raw == "null":
        log.info("  [查看职位] 未找到「查看职位」按钮，跳过")
        return None
    btn = json.loads(raw)
    if not btn["visible"]:
        log.info("  [查看职位] 按钮不可见，跳过")
        return None

    browser = pychrome.Browser(url=CDP_CHAT_URL)
    before_ids = {t["id"] for t in requests.get(f"{CDP_CHAT_URL}/json", timeout=5).json()}

    cdp_click(tab, btn["x"], btn["y"])
    log.info("  [查看职位] 已点击，等待详情页打开...")

    detail_meta = None
    deadline = time.time() + 8.0
    while time.time() < deadline:
        for t in requests.get(f"{CDP_CHAT_URL}/json", timeout=5).json():
            if (t["id"] not in before_ids
                    and t.get("type") == "page"
                    and "/job_detail/" in t.get("url", "")):
                detail_meta = t
                break
        if detail_meta:
            break
        time.sleep(0.5)

    if not detail_meta:
        log.info("  [查看职位] 超时未检测到详情页，跳过")
        return None

    detail_tab = next((t for t in browser.list_tab() if t.id == detail_meta["id"]), None)
    if detail_tab is None:
        log.info("  [查看职位] 未能连接详情页标签，跳过")
        return None

    detail_tab.start()
    try:
        for _ in range(10):
            if evaluate(detail_tab, "!!document.querySelector('.job-name')"):
                break
            time.sleep(0.5)
        raw_detail = evaluate(detail_tab, _JS_EXTRACT_JOB_DETAIL)
    finally:
        detail_tab.stop()
        try:
            browser.close_tab(detail_meta["id"])
        except Exception as e:
            log.warning(f"  [查看职位] 关闭详情页标签失败: {e}")
        try:
            browser.activate_tab(tab)
        except Exception as e:
            log.warning(f"  [查看职位] 切回聊天页标签失败: {e}")

    if not raw_detail:
        log.info("  [查看职位] 详情页提取失败")
        return None

    detail = json.loads(raw_detail)
    log.info(f"  [查看职位] 已获取详情：{detail.get('jobName','')} "
             f"@ {detail.get('companyName','')}（JD {len(detail.get('jd',''))} 字）")
    return detail


# ── 会话操作主入口 ────────────────────────────────────────────────────────────

def execute_session_actions(
    tab,
    my_texts: list[dict],
    boss_texts: list[dict],
    last_is_boss: bool,
    resume_already_sent: bool,
    resume: str,
    jd: str,
    chat_info: dict,
    messages: list[dict],
):
    """
    执行会话操作阶段（场景C / 场景A / 场景B，统一基于「有JD」上下文）。
    各分支内不调用 upsert_chat / read_messages。

    Step 1（无条件）：handle_interactive_cards 依次点击微信/沟通意向等非简历卡片
      的「同意」（每次重新读取坐标），简历请求卡始终跳过、不在此处处理。
    Step 2：根据 my_texts / boss_texts 进入对应分支，完成 AI 和发消息（含简历操作）。
    """
    company = chat_info.get("companyName", "")

    # 供打印最新 boss 消息文字用
    _last_text_msg = next(
        (m for m in reversed(messages)
         if not m["isSystem"] and not m["isCard"] and m.get("text")),
        None
    )

    def _log_box(title, text):
        bar = "─" * max(0, 54 - len(title))
        log.info(f"  ┌─ {title} {bar}")
        for line in text.split("\n"):
            log.info(f"  │  {line}")
        log.info("  └" + "─" * 57)

    def _type_and_log(tab, text, shot_suffix):
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

    # ══════════════════════════════════════════════════════════════════════════
    # Step 1：处理除简历卡片外所有可点击「同意」的交互卡片（无条件，优先于所有分支）
    # ══════════════════════════════════════════════════════════════════════════
    handle_interactive_cards(tab)
    resume_sent_now = 0

    # ══════════════════════════════════════════════════════════════════════════
    # Step 2：按 my_texts / boss_texts 分支处理 AI 和发消息（统一在「有JD」前提下）
    # ══════════════════════════════════════════════════════════════════════════

    def _send_self_promo(action_label: str):
        if not REPLY_ENABLED:
            log.info("  [AI] REPLY_ENABLED=False，跳过 API 调用和消息发送")
            return
        log.info(f"  [AI] 生成{action_label}中...")
        ai_result = call_ai(chat_info, jd, resume, messages, need_reply=False, need_self_promo=True)
        log.info(f"  [AI] 倾向分: {ai_result['tendency_score']}/100  {ai_result['reasoning']}")
        promo = ai_result.get("self_promo", "")
        if promo:
            log.info(f"  [AI] 生成成功（{len(promo)} 字）")
            _type_and_log(tab, promo, company[:10])
        else:
            log.info("  [AI] 生成结果为空，跳过")

    if not my_texts:
        # 场景C: Boss 主动发起，我方无消息 → 发简历 + AI 自我介绍
        log.info("  [场景C] Boss 主动发起，我方无消息 → 发简历 + AI 自我介绍")
        ok = execute_resume_action(tab)
        if ok:
            resume_sent_now = 1
            random_delay(1.0, 2.0)
        _send_self_promo("自我介绍")

    elif not boss_texts:
        # 场景A: 我方主动发起，Boss 尚未回复 → AI 自我推荐
        log.info("  [场景A] 我方主动发起，Boss 尚未回复 → AI 自我推荐")
        _send_self_promo("自我推荐")

    else:
        # 场景B: 双方均有消息
        log.info("  [场景B] 双方均有消息")
        if resume_already_sent:
            log.info("  → 简历已投递过，跳过简历环节")
        else:
            ok = execute_resume_action(tab)
            if ok:
                resume_sent_now = 1
                random_delay(1.0, 2.0)

        need_self_promo = resume_sent_now == 1
        need_reply      = last_is_boss
        log.info(f"  需要自我推荐: {'是' if need_self_promo else '否'}")
        log.info(f"  需要回复:     {'是' if need_reply else '否'}"
                 f"{'（最后发言是我）' if _last_text_msg and _last_text_msg['isSelf'] else ''}")
        if need_reply and _last_text_msg:
            log.info(f"  最新Boss消息: {_last_text_msg['text'][:50]!r}")

        if not REPLY_ENABLED:
            log.info("  [AI] REPLY_ENABLED=False，跳过 API 调用和消息发送")
        elif not need_self_promo and not need_reply:
            log.info("  [AI] 无需自我推荐也无需回复，跳过 API 调用")
        else:
            log.info("  [AI] 分析中...")
            ai_result = call_ai(
                chat_info, jd, resume, messages,
                need_reply=need_reply,
                need_self_promo=need_self_promo,
            )
            log.info(f"  [AI] 倾向分: {ai_result['tendency_score']}/100  {ai_result['reasoning']}")
            if need_self_promo and ai_result.get("self_promo"):
                _type_and_log(tab, ai_result["self_promo"], company[:10])
                if need_reply and ai_result.get("reply"):
                    random_delay(2.0, 3.0)
            if need_reply and ai_result.get("reply"):
                _type_and_log(tab, ai_result["reply"], company[:10])

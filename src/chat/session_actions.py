import sys, json, re, time, random
from pathlib import Path
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (API_KEY, API_BASE_URL, AI_MODEL,
                    FIXED_SELF_INTRO, FIXED_FOLLOWUP, DISCLAIMER,
                    REPLY_ENABLED, SEND_ENABLED)
from shared.cdp_utils import evaluate, cdp_click, random_delay, read_messages
from shared.logger import log

# ── 常量 ──────────────────────────────────────────────────────────────────────

INPUT_SEL = "div.chat-input[contenteditable='true']"
SEND_SEL  = "button.btn-send"

_ai_client: OpenAI | None = None

# ── AI Prompt ─────────────────────────────────────────────────────────────────

_SYS_SELF_PROMO = """\
你是专业求职助手。用户已主动向该HR发起沟通，但HR尚未回复。
根据职位JD和用户简历，生成一段自我推荐文字：
- 100-200字，语气自然专业，以第一人称书写
- 突出与岗位最相关的 2-3 个经历或技能
- 结尾可以表达期待进一步沟通
只输出消息文字，不含任何其他内容。\
"""

_SYS_PROMPT = """\
你是专业求职助手，帮助用户与招聘HR进行自然、专业的中文沟通。

根据职位JD、用户简历、完整聊天记录，完成以下任务：
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

# 无JD 时使用：去掉 JD 分析要求，避免模型因上下文为空而返回自然语言
_SYS_PROMPT_NO_JD = """\
你是专业求职助手，帮助用户与招聘HR进行自然、专业的中文沟通。

当前情况：未能获取完整职位JD，仅有公司名称、用户简历和聊天记录可供参考。

根据用户简历和聊天记录，完成以下任务：
1. 评估HR倾向性分数（0-100）：
   - 0-30  ：不感兴趣/敷衍
   - 30-60 ：例行流程/一般
   - 60-80 ：较感兴趣/主动跟进
   - 80-100：非常感兴趣/积极推进
2. 若 need_self_promo=true：生成简历投递后的自我推荐，100-150字，自然专业，
   突出简历中最有竞争力的 2-3 个经历或技能，结尾表达期待进一步了解岗位详情
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


def call_ai_self_promo(boss_info: dict, jd: str, resume: str) -> str:
    user_content = (
        f"【职位JD】\n公司：{boss_info.get('companyName','')}\n{jd[:2500]}\n\n"
        f"【我的简历】\n{resume[:2500]}"
    )
    try:
        resp = _get_client().chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": _SYS_SELF_PROMO},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=400,
        )
        result = (resp.choices[0].message.content or "").strip()
        if not result:
            log.warning("  [AI] 自我推荐响应为空")
        return result
    except Exception as e:
        log.error(f"  [AI] 自我推荐生成失败: {e}")
        return ""


def call_ai(
    boss_info: dict,
    jd: str,
    resume: str,
    messages: list[dict],
    need_reply: bool,
    need_self_promo: bool = False,
) -> dict:
    """调用 AI，返回 {self_promo, reply, tendency_score, reasoning}。
    jd 为空时自动切换到无JD专用 prompt。
    """
    has_jd_ctx = bool(jd and jd.strip())
    sys_prompt = _SYS_PROMPT if has_jd_ctx else _SYS_PROMPT_NO_JD

    if has_jd_ctx:
        jd_section = f"【职位JD】\n公司：{boss_info.get('companyName','')}\n{jd[:2500]}\n\n"
    else:
        jd_section = f"【说明】无完整JD，仅供参考\n公司：{boss_info.get('companyName','')}\n\n"

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
                {"role": "system", "content": sys_prompt},
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
    处理当前聊天中所有可点击「同意」的交互卡片。

    流程（x = 可点击同意卡片总数）：
      - 若含简历请求卡（cardType='resume'）：
          先循环点击其余 x-1 张（每轮重新读取坐标，跳过 resume）
          再重新读取简历卡坐标，点击「同意」并处理弹窗
      - 若不含简历请求卡：
          循环点击全部 x 张（每轮重新读取坐标）
      非简历卡点击后等待 1.5-2.5s（系统可能自动插入消息，坐标会变化）。

    返回 True = 简历弹窗已确认发送；False = 未处理简历卡（含调试模式）。
    """
    cards = _read_agree_cards(tab)
    x = len(cards)
    if x == 0:
        return False

    has_resume = any(c["cardType"] == "resume" for c in cards)
    log.info(f"  [卡片交互] 共 {x} 张可点击同意卡片  含简历卡: {has_resume}")
    for c in cards:
        label = _CARD_TYPE_LABEL.get(c["cardType"], f"未知({c['cardType']})")
        log.info(f"    {label}  center=({c['x']},{c['y']})")

    # ── 循环点击非简历卡片 ────────────────────────────────────────────────────
    click_count = x - 1 if has_resume else x
    for i in range(click_count):
        # 每轮重新读取，跳过 resume 类型，取第一个
        fresh = [c for c in _read_agree_cards(tab) if c["cardType"] != "resume"]
        if not fresh:
            log.info(f"  [卡片交互] 第 {i+1} 轮：非简历卡片已全部处理，提前结束")
            break
        card  = fresh[0]
        label = _CARD_TYPE_LABEL.get(card["cardType"], f"未知({card['cardType']})")
        log.info(f"  [卡片交互] 点击第 {i+1}/{click_count} 张「{label}」"
                 f"  center=({card['x']},{card['y']})")
        cdp_click(tab, card["x"], card["y"])
        random_delay(1.5, 2.5)

    if not has_resume:
        return False

    # ── 重新读取简历卡坐标，点击「同意」并处理弹窗 ───────────────────────────
    resume_cards = [c for c in _read_agree_cards(tab) if c["cardType"] == "resume"]
    if not resume_cards:
        log.info("  [卡片交互] 重新读取：简历请求卡片已消失或已处理")
        return False

    card = resume_cards[0]
    log.info(f"  [卡片交互] 点击简历请求「同意」  center=({card['x']},{card['y']})")
    cdp_click(tab, card["x"], card["y"])
    random_delay(1.0, 1.5)
    return handle_resume_dialog(tab)


# ── 会话操作主入口 ────────────────────────────────────────────────────────────

def execute_session_actions(
    tab,
    has_jd: bool,
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
    执行会话操作阶段（无JD-C / 无JD-A / 无JD-B / 模式A / 模式B）。
    各分支内不调用 upsert_chat / read_messages。

    Step 1（无条件）：handle_interactive_cards 处理所有可点击「同意」卡片。
      - 微信/沟通意向等非简历卡：依次点击，每次重新读取坐标
      - 简历请求卡：最后点击「同意」并处理弹窗
    Step 2：根据 has_jd / my_texts / boss_texts 进入对应分支，完成 AI 和发消息。
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
    # Step 1：处理所有可点击「同意」的交互卡片（无条件，优先于所有分支）
    # ══════════════════════════════════════════════════════════════════════════
    resume_sent_now = 1 if handle_interactive_cards(tab) else 0

    # ══════════════════════════════════════════════════════════════════════════
    # Step 2：按 has_jd / my_texts / boss_texts 分支处理 AI 和发消息
    # ══════════════════════════════════════════════════════════════════════════

    if not has_jd:
        label_nojd = "未在 jobs 表" if not chat_info.get("encryptJobId") else "JD 为空"
        log.info(f"  [无JD] {label_nojd}")

        if not my_texts:
            # 无JD-C: boss 主动发起，我方无消息
            log.info("  [无JD-C] Boss 主动发起，我方无消息 → 发简历 + 固定自我介绍")
            if not resume_sent_now:
                ok = execute_resume_action(tab)
                if ok:
                    resume_sent_now = 1
                    random_delay(1.0, 2.0)
            _type_and_log(tab, FIXED_SELF_INTRO, company[:10])

        elif not boss_texts:
            # 无JD-A: 我主动发起，boss 未回复
            log.info("  [无JD-A] 我方主动发起，Boss 未回复 → 固定跟进话术")
            _type_and_log(tab, FIXED_FOLLOWUP, company[:10])

        else:
            # 无JD-B: 双方均有消息
            log.info("  [无JD-B] 双方均有消息")
            if resume_already_sent or resume_sent_now:
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
                log.info("  [AI] 分析中（无JD）...")
                ai_result = call_ai(
                    chat_info, "", resume, messages,
                    need_reply=need_reply,
                    need_self_promo=need_self_promo,
                )
                log.info(f"  [AI] 倾向分: {ai_result['tendency_score']}/100"
                         f"  {ai_result['reasoning']}")
                if need_self_promo and ai_result.get("self_promo"):
                    _type_and_log(tab, ai_result["self_promo"], company[:10])
                    if need_reply and ai_result.get("reply"):
                        random_delay(2.0, 3.0)
                if need_reply and ai_result.get("reply"):
                    _type_and_log(tab, ai_result["reply"], company[:10])

    elif not boss_texts:
        # 模式A（有JD）: 仅我方有消息，Boss 尚未回复
        log.info("  [模式A] 有JD，Boss 尚未回复 → API 自我推荐")
        if not REPLY_ENABLED:
            log.info("  [AI] REPLY_ENABLED=False，跳过 API 调用和消息发送")
        else:
            log.info("  [AI] 生成自我推荐中...")
            promo = call_ai_self_promo(chat_info, jd, resume)
            if promo:
                log.info(f"  [AI] 生成成功（{len(promo)} 字）")
                _type_and_log(tab, promo, company[:10])
            else:
                log.info("  [AI] 生成结果为空，跳过")

    else:
        # 模式B（有JD）: 双方均有消息
        log.info("  [模式B] 有JD，双方均有消息")
        if resume_already_sent or resume_sent_now:
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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.logger import log
from shared.cdp_utils import random_delay
from config import REPLY_ENABLED
from chat.ai import call_ai
from chat.messaging import type_and_log
from chat.resume_attachment import execute_resume_action
from chat.interactive_cards import handle_interactive_cards

# 重新导出，供 session_processor 等模块使用（保持原有 import 路径不变）
from chat.job_detail_fetch import fetch_job_detail_via_view_job  # noqa: F401


# ── 会话操作主入口 ────────────────────────────────────────────────────────────

def execute_session_actions(
    tab,
    my_texts: list[dict],
    boss_texts: list[dict],
    last_is_boss: bool,
    resume_already_sent: bool,
    resume: str,
    jd: str,
    salary: str,
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
    target  = {"name": chat_info.get("name", ""), "companyName": company}

    # 供打印最新 boss 消息文字用
    _last_text_msg = next(
        (m for m in reversed(messages)
         if not m["isSystem"] and not m["isCard"] and m.get("text")),
        None
    )

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
        ai_result = call_ai(chat_info, jd, resume, messages, need_reply=False, need_self_promo=True, salary=salary)
        promo = ai_result.get("self_promo", "")
        if promo:
            log.info(f"  [AI] 生成成功（{len(promo)} 字）")
            type_and_log(tab, promo, company[:10])
        else:
            log.info("  [AI] 生成结果为空，跳过")

    if not my_texts:
        # 场景C: Boss 主动发起，我方无消息 → 发简历 + AI 自我介绍
        log.info("  [场景C] Boss 主动发起，我方无消息 → 发简历 + AI 自我介绍")
        ok = execute_resume_action(tab, company=company, jd=jd, target=target)
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
            ok = execute_resume_action(tab, company=company, jd=jd, target=target)
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
                salary=salary,
            )
            if need_self_promo and ai_result.get("self_promo"):
                type_and_log(tab, ai_result["self_promo"], company[:10])
                if need_reply and ai_result.get("reply"):
                    random_delay(2.0, 3.0)
            if need_reply and ai_result.get("reply"):
                type_and_log(tab, ai_result["reply"], company[:10])


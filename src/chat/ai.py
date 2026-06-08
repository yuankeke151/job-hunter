import sys, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AI_MODEL
from shared.ai_client import get_client
from shared.logger import log

# ── AI Prompt ─────────────────────────────────────────────────────────────────

_SYS_PROMPT = """\
你是专业求职助手，帮助用户与招聘HR进行自然、专业的中文沟通。

根据职位JD（含薪资范围，如有）、用户简历、完整聊天记录，完成以下任务：
0. 若提供了薪资范围，可作为生成回复内容的参考依据
   （如薪资明显契合或聊天中提到薪资话题时自然回应，无需主动炫耀或纠结数字）
1. 若 need_self_promo=true：生成简历投递后的自我推荐，100-150字，自然专业，
   突出与岗位最相关的 2-3 个经历或技能，结尾表达期待沟通
2. 若 need_reply=true：针对HR最新消息生成回复，50-150字，语气自然

只输出合法JSON，不含任何markdown或额外文字：
{"self_promo": "...", "reply": "..."}

不需要的字段填空字符串 ""。\
"""

# ── AI 工具 ───────────────────────────────────────────────────────────────────

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
    salary: str = "",
) -> dict:
    """调用 AI，返回 {self_promo, reply}。"""
    salary_line = f"薪资范围：{salary}\n" if salary else ""
    jd_section  = f"【职位JD】\n公司：{boss_info.get('companyName','')}\n{salary_line}{jd[:2500]}\n\n"

    user_content = (
        f"need_self_promo: {'true' if need_self_promo else 'false'}\n"
        f"need_reply: {'true' if need_reply else 'false'}\n\n"
        f"{jd_section}"
        f"【我的简历】\n{resume[:2500]}\n\n"
        f"【完整聊天记录】\n{_fmt_history(messages)}"
    )
    _empty = {"self_promo": "", "reply": ""}
    try:
        resp = get_client().chat.completions.create(
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
            "self_promo" : result.get("self_promo", ""),
            "reply"      : result.get("reply", ""),
        }
    except json.JSONDecodeError as e:
        log.error(f"  [AI] JSON 解析失败: {e}  原始响应: {raw!r:.100}")
        return _empty
    except Exception as e:
        log.error(f"  [AI] 调用失败: {e}")
        return _empty

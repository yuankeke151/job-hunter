"""
resume_tailor.py — 按目标岗位 JD 用 AI 微调简历内容，并渲染为 PDF。

仅在 config.GENERATE_TAILORED_RESUME=True 时被调用。

流程：
  1. 调用 AI（复用与 session_actions.call_ai 相同的客户端/模型配置），
     基于"原始简历文本 + 目标岗位 JD"生成一份微调后的简历正文（要求保持真实信息，不编造）
  2. 用 Playwright 启动无头 Chromium，把生成内容套入简单 HTML 模板，
     调用 page.pdf() 渲染为 PDF（原生支持中文/Unicode，优于手写 PDF 字节流方案）
  3. 保存到 output_resumes/，命名 “袁柯_公司名称_生成时间.pdf”，返回 Path；失败返回 None
"""
import sys, json, re
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AI_MODEL, OUTPUT_RESUMES_DIR
from shared.ai_client import get_client
from shared.logger import log


_SYS_PROMPT = """\
你是专业简历顾问。根据用户提供的【原始简历】和【目标岗位JD】，生成一份针对该岗位微调过的简历正文。

要求：
1. 必须基于原始简历的真实信息进行调整，严禁编造不存在的经历、技能或数据
2. 突出与目标岗位 JD 最相关的经历、项目和技能，可调整措辞、顺序和详略
3. 输出结构化的简历正文文本，分段清晰（如"基本信息""技能""工作经历""项目经历""教育背景"等），
   每段一个标题，标题与正文之间用换行分隔，不要使用 markdown 符号（不要 # 和 **）

只输出简历正文文本本身，不要任何解释、前后缀或代码块标记。\
"""


def _generate_resume_text(jd: str, original_resume_text: str) -> str:
    user_content = (
        f"【目标岗位JD】\n{jd[:3000]}\n\n"
        f"【原始简历】\n{original_resume_text[:4000]}"
    )
    try:
        resp = get_client().chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": _SYS_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        return raw
    except Exception as e:
        log.error(f"  [定制简历] AI 生成简历正文失败: {e}")
        return ""


def _render_pdf(text: str, out_path: Path) -> bool:
    """用 Playwright 无头 Chromium 把简历文本渲染为 PDF（原生支持中文）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("  [定制简历] 未安装 playwright，无法渲染 PDF（pip install playwright 并执行 playwright install chromium）")
        return False

    # 简单分段：空行分隔为多个段落，首行作为标题
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    title = "袁柯 · 简历"
    body_html = "".join(
        f"<p>{_escape_html(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
        body {{ font-family: "Microsoft YaHei", "SimSun", sans-serif; font-size: 13px;
                line-height: 1.6; color: #222; padding: 32px; }}
        h1 {{ font-size: 20px; margin-bottom: 16px; }}
        p {{ margin: 0 0 12px 0; white-space: pre-wrap; }}
    </style></head><body><h1>{title}</h1>{body_html}</body></html>"""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out_path), format="A4",
                     margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"})
            browser.close()
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        log.error(f"  [定制简历] Playwright 渲染 PDF 失败: {e}")
        return False


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def generate_tailored_resume(company: str, jd: str, original_resume_text: str) -> Path | None:
    """生成定制简历 PDF，成功返回文件 Path，失败返回 None。"""
    if not jd:
        return None
    log.info(f"  [定制简历] 开始为「{company}」生成定制简历...")
    text = _generate_resume_text(jd, original_resume_text)
    if not text:
        log.warning("  [定制简历] AI 生成内容为空，放弃")
        return None

    safe_company = re.sub(r'[\\/:*?"<>|]', "_", company).strip() or "未知公司"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_RESUMES_DIR / f"袁柯_{safe_company}_{ts}.pdf"

    if not _render_pdf(text, out_path):
        log.warning("  [定制简历] PDF 渲染失败，放弃")
        return None

    log.info(f"  [定制简历] ✓ 已生成: {out_path.name} ({out_path.stat().st_size} bytes)")
    return out_path

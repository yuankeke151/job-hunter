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
import sys, re
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AI_MODEL, OUTPUT_RESUMES_DIR
from shared.ai_client import get_client
from shared.logger import log


_SYS_PROMPT = """\
你是专业简历顾问。根据用户提供的【原始简历】和【目标岗位JD】，生成一份针对该岗位微调过的简历 HTML 片段。

输出格式要求（严格遵守）：
1. 只输出 HTML body 内容片段，不要 <!DOCTYPE>/<html>/<head>/<body> 等外层标签
2. 使用以下标签，不允许使用其他标签：
   - <h1> ：仅用于姓名（一个）
   - <div class="contact"> ：联系方式一行（紧跟 h1，格式：电话 · 邮箱 · 城市）
   - <h2> ：各节标题（如"技能""工作经历""项目经历""教育背景"）
   - <p> ：段落正文
   - <ul><li> ：列表项
   - <strong> ：加粗（公司名、职位名等）
3. 必须基于原始简历的真实信息进行调整，严禁编造不存在的经历、技能或数据
4. 突出与目标岗位 JD 最相关的经历、项目和技能，可调整措辞、顺序和详略
5. 不使用 markdown 符号（不要 # ** ` 等）
6. 不要代码块标记（不要 ```）

示例结构：
<h1>姓名</h1>
<div class="contact">电话 · 邮箱 · 城市</div>
<h2>技能</h2>
<ul><li>Python / SQL / ...</li></ul>
<h2>工作经历</h2>
<p><strong>公司名</strong> · 职位 · 时间</p>
<ul><li>工作内容...</li></ul>
<h2>教育背景</h2>
<p><strong>学校</strong> · 专业 · 学历 · 时间</p>

只输出 HTML 片段本身，不要任何解释或前后缀。\
"""


def _generate_resume_html(jd: str, original_resume_text: str) -> str:
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
            max_tokens=2500,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        return raw
    except Exception as e:
        log.error(f"  [定制简历] AI 生成简历失败: {e}")
        return ""


def _render_pdf(html_fragment: str, out_path: Path) -> bool:
    """用 Playwright 无头 Chromium 把 HTML 片段渲染为格式化 PDF。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("  [定制简历] 未安装 playwright，无法渲染 PDF（pip install playwright 并执行 playwright install chromium）")
        return False

    css = """
    @page { size: A4; margin: 18mm 20mm; }
    body {
        font-family: "Microsoft YaHei", "SimSun", "Noto Sans CJK SC", sans-serif;
        font-size: 12px;
        line-height: 1.75;
        color: #1a1a1a;
    }
    h1 {
        text-align: center;
        font-size: 22px;
        margin: 0 0 4px;
        letter-spacing: 3px;
    }
    .contact {
        text-align: center;
        font-size: 11px;
        color: #555;
        margin-bottom: 16px;
        letter-spacing: 0.5px;
    }
    h2 {
        font-size: 13px;
        font-weight: 700;
        border-bottom: 1.5px solid #333;
        padding-bottom: 3px;
        margin: 16px 0 6px;
        letter-spacing: 1px;
    }
    p { margin: 2px 0 6px; }
    ul { margin: 2px 0 8px; padding-left: 18px; }
    li { margin-bottom: 3px; }
    strong { font-weight: 600; }
    """
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<style>{css}</style>
</head><body>
{html_fragment}
</body></html>"""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(
                path=str(out_path),
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            browser.close()
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        log.error(f"  [定制简历] Playwright 渲染 PDF 失败: {e}")
        return False


def generate_tailored_resume(company: str, jd: str, original_resume_text: str) -> Path | None:
    """生成定制简历 PDF，成功返回文件 Path，失败返回 None。"""
    if not jd:
        return None
    log.info(f"  [定制简历] 开始为「{company}」生成定制简历...")
    html_fragment = _generate_resume_html(jd, original_resume_text)
    if not html_fragment:
        log.warning("  [定制简历] AI 生成内容为空，放弃")
        return None

    safe_company = re.sub(r'[\\/:*?"<>|]', "_", company).strip() or "未知公司"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_RESUMES_DIR / f"袁柯_{safe_company}_{ts}.pdf"

    if not _render_pdf(html_fragment, out_path):
        log.warning("  [定制简历] PDF 渲染失败，放弃")
        return None

    log.info(f"  [定制简历] ✓ 已生成: {out_path.name} ({out_path.stat().st_size} bytes)")
    return out_path

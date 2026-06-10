"""
test_resume_html_format.py — 测试「AI 输出 HTML 片段→渲染为格式化 PDF」新方案。

不改动任何现有代码，独立验证：
  1. AI 是否能按指令输出合法 HTML 片段（h1 / h2 / ul / p）
  2. Playwright 能否把该 HTML 渲染为有排版的 PDF

运行：
  python src/debug/test_resume_html_format.py

结果保存到 output_resumes/test_html_format_<时间戳>.pdf
"""
import sys, re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AI_MODEL, OUTPUT_RESUMES_DIR
from shared.ai_client import get_client
from shared.logger import log

# ── 示例 JD（简化版，实际使用时从数据库读取）──────────────────────────────────
_SAMPLE_JD = """\
数据分析师（Python 方向）
职责：
- 负责用户行为数据的采集、清洗和分析，产出业务洞察报告
- 使用 Python（pandas/numpy/scipy）构建指标体系及自动化分析流水线
- 与业务方沟通需求，将分析结论转化为可落地的产品/运营建议
要求：
- 3年以上数据分析经验，熟悉 Python 数据分析生态
- 熟悉 SQL，有大数据平台（Hive/Spark）使用经验优先
- 良好的数据敏感度与沟通表达能力
- 本科及以上学历
"""

# ── 示例简历（纯文本占位，实际使用时从 resume/袁柯.txt 读取）──────────────────
_SAMPLE_RESUME = """\
袁柯
电话：138-xxxx-xxxx  邮箱：yuanke@example.com  北京

技能
Python（pandas/numpy/matplotlib/sklearn）、SQL、Hive、Spark、Tableau、Git

工作经历
XX科技有限公司  数据分析师  2022.07 - 至今
- 负责用户增长漏斗分析，优化转化率提升 15%
- 搭建自动化 ETL 流水线，数据处理效率提升 40%
- 产出月度运营分析报告，支持产品迭代决策

YY互联网公司  数据实习生  2021.09 - 2022.06
- 协助构建用户画像模型，准确率达 82%
- 编写 SQL 报表脚本，减少人工统计工作量 60%

项目经历
用户流失预测模型
- 使用 LightGBM 对 50 万用户数据建模，AUC 0.87
- 接入业务系统，7 日内预警召回用户 3000+

教育背景
北京邮电大学  统计学  本科  2018.09 - 2022.06
"""

# ── 新方案 System Prompt（输出 HTML 片段）────────────────────────────────────
_NEW_SYS_PROMPT = """\
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
3. 不使用 markdown 符号（不要 # ** ` 等）
4. 不要代码块标记（不要 ```）
5. 必须基于原始简历的真实信息，严禁编造经历或数据

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
    """调用 AI 生成 HTML 格式简历片段。"""
    user_content = (
        f"【目标岗位JD】\n{jd[:3000]}\n\n"
        f"【原始简历】\n{original_resume_text[:4000]}"
    )
    resp = get_client().chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": _NEW_SYS_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=2500,
    )
    raw = (resp.choices[0].message.content or "").strip()
    # 去除可能的 ```html ... ``` 包裹
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()
    return raw


def _render_html_pdf(html_fragment: str, out_path: Path) -> bool:
    """用 Playwright 把 HTML 片段渲染为格式化 PDF。"""
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
    p {
        margin: 2px 0 6px;
    }
    ul {
        margin: 2px 0 8px;
        padding-left: 18px;
    }
    li {
        margin-bottom: 3px;
    }
    strong {
        font-weight: 600;
    }
    """
    full_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<style>{css}</style>
</head><body>
{html_fragment}
</body></html>"""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("未安装 playwright，请执行: pip install playwright && playwright install chromium")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(full_html, wait_until="load")
        page.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        browser.close()
    return out_path.exists() and out_path.stat().st_size > 0


def main():
    print("=== 测试新方案：AI 输出 HTML 片段 → 格式化 PDF ===\n")

    print("[1/3] 调用 AI 生成 HTML 简历片段...")
    html_frag = _generate_resume_html(_SAMPLE_JD, _SAMPLE_RESUME)

    if not html_frag:
        print("❌ AI 返回内容为空")
        return

    print(f"[2/3] AI 返回内容（{len(html_frag)} 字符）：\n")
    print("-" * 60)
    print(html_frag[:2000])
    if len(html_frag) > 2000:
        print(f"... （已截断，完整 {len(html_frag)} 字符）")
    print("-" * 60)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_RESUMES_DIR / f"test_html_format_{ts}.pdf"

    print(f"\n[3/3] 渲染 PDF → {out_path.name} ...")
    ok = _render_html_pdf(html_frag, out_path)

    if ok:
        print(f"\n✅ 成功！文件大小: {out_path.stat().st_size} bytes")
        print(f"   路径: {out_path}")
    else:
        print("\n❌ PDF 渲染失败，请查看日志")


if __name__ == "__main__":
    main()

"""
analyzer.py — 第三阶段：Claude API 匹配度分析

读取简历 PDF（首次提取后缓存为同名 .txt，后续直接读缓存），
对每个 JD 调用 API，返回结构化 JSON 分析结果。
"""
import sys
import json
import re
from pathlib import Path

import pdfplumber
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import API_KEY, API_BASE_URL, AI_MODEL, RESUME_PATH, SCORE_THRESHOLD
from shared.logger import log as logger

_RESUME_TXT = RESUME_PATH.with_suffix(".txt")
_resume_cache: str = ""
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
    return _client


def _load_resume() -> str:
    global _resume_cache
    if _resume_cache:
        return _resume_cache
    if _RESUME_TXT.exists():
        _resume_cache = _RESUME_TXT.read_text(encoding="utf-8")
        logger.info(f"简历从缓存读取: {_RESUME_TXT}")
        return _resume_cache
    if not RESUME_PATH.exists():
        raise FileNotFoundError(f"简历文件不存在: {RESUME_PATH}")
    with pdfplumber.open(str(RESUME_PATH)) as pdf:
        _resume_cache = "\n".join(page.extract_text() or "" for page in pdf.pages)
    _RESUME_TXT.write_text(_resume_cache, encoding="utf-8")
    logger.info(f"简历已提取并缓存至: {_RESUME_TXT}")
    return _resume_cache


_SYSTEM_PROMPT = """\
你是专业职业顾问，分析候选人简历与目标职位的匹配度。
若提供了薪资范围，可作为匹配度评估的参考依据之一
（如薪资明显低于候选人预期或行业水平，可在 skip_reason 中说明）。
请只输出合法 JSON，格式如下（不含任何 markdown 或额外文字）：
{
  "match_score": <0-100 整数>,
  "key_matches": ["匹配点1", "匹配点2"],
  "missing_skills": ["缺失技能1"],
  "skip_reason": "<不推荐投递时填写原因，否则留空字符串>"
}\
"""


def analyze_job(company: str, position: str, jd: str, salary: str = "") -> dict:
    """
    分析简历与 JD 匹配度，返回：
    {
        "match_score": 0-100,
        "should_apply": True/False,
        "key_matches": [...],
        "missing_skills": [...],
        "skip_reason": "..."
    }

    salary: 解码成功时传入薪资描述（如 "25-50K·14薪"），作为匹配度参考一并发给 API；
            解码失败或未提供时传空字符串，不出现在 prompt 中。
    """
    resume      = _load_resume()
    salary_line = f"薪资范围：{salary}\n" if salary else ""
    user_msg = (
        f"候选人简历：\n{resume}\n\n"
        f"目标公司：{company}\n"
        f"目标职位：{position}\n"
        f"{salary_line}\n"
        f"职位描述：\n{jd[:3000]}"
    )

    try:
        resp = _get_client().chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=500,
        )
        raw = resp.choices[0].message.content.strip()

        # 去掉模型可能包裹的 markdown 代码块
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败: {e}\n原始响应: {raw!r}")
        return {
            "match_score": 0,
            "should_apply": False,
            "key_matches": [],
            "missing_skills": [],
            "skip_reason": f"API 返回格式异常: {e}",
        }
    except Exception as e:
        logger.error(f"analyze_job 调用失败: {e}")
        return {
            "match_score": 0,
            "should_apply": False,
            "key_matches": [],
            "missing_skills": [],
            "skip_reason": f"API 调用异常: {e}",
        }

    score = min(max(int(result.get("match_score", 0)), 0), 100)
    return {
        "match_score": score,
        "should_apply": score >= SCORE_THRESHOLD,
        "key_matches": result.get("key_matches") or [],
        "missing_skills": result.get("missing_skills") or [],
        "skip_reason": result.get("skip_reason") or "",
    }

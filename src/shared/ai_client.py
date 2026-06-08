"""
ai_client.py — 共用的 OpenAI 兼容客户端单例

scanner.analyzer / chat.session_actions / chat.resume_tailor 三处原各自实现了
完全相同的懒加载单例（_ai_client + _get_client），现统一收敛到这里。
"""
from openai import OpenAI

from config import API_KEY, API_BASE_URL

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
    return _client
